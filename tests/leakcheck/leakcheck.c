/*
 * leakcheck.c -- a tiny malloc/free interposer for Sushi's leak gate.
 *
 * Loaded via LD_PRELOAD (Linux) / DYLD_INSERT_LIBRARIES (macOS) when the test
 * runner re-runs a leak-gated binary. It wraps malloc/calloc/realloc/free,
 * tracks the outstanding byte balance, and at process exit prints ONE line to
 * stderr:
 *
 *     SUSHI_LEAKCHECK: leaked=<bytes> blocks=<n>
 *
 * The runner parses that line (see tests/enhanced_test_runner.py::_check_leaks)
 * and fails the test if leaked > 0.
 *
 * Noise-free accounting: every wrapped allocation records the immediate caller
 * (__builtin_return_address(0)). Only allocations whose caller lies in the MAIN
 * EXECUTABLE's text range are tracked. All of Sushi's own code -- backend
 * codegen output AND the stdlib bitcode, which is merged into the module before
 * object emission -- lives in the main image, so its allocations count; libc
 * internals (stdio buffers, locale, the ObjC runtime) are called from other
 * images and are ignored. A correct RAII program nets exactly zero, no baseline.
 *
 * We return the allocator's pointer UNMODIFIED (no header/offset) and remember
 * {pointer -> size} for tracked blocks in a side table. Offsetting the pointer
 * would break malloc_size()/malloc introspection that libSystem and the ObjC
 * runtime perform on every allocation.
 */

#define _GNU_SOURCE
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdio.h>
#include <stdatomic.h>
#include <dlfcn.h>

#if defined(__APPLE__)
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#else
#include <link.h>
#endif

static void *(*real_malloc)(size_t);
static void  (*real_free)(void *);
static void *(*real_calloc)(size_t, size_t);
static void *(*real_realloc)(void *, size_t);

static uintptr_t g_text_lo, g_text_hi;
static int g_ready;

/* ---- outstanding-balance side table (tracked blocks only) ---------------- */
#define TAB_CAP  (1u << 16)
#define TAB_MASK (TAB_CAP - 1u)

/* Slot states. A freed slot KEEPS its key and becomes ST_DEAD rather than being
 * overwritten with a tombstone sentinel: that is what makes a second free of the
 * same pointer distinguishable from a free of memory we never tracked. The old
 * scheme lost the key on removal, so both cases probed to an empty slot and were
 * reported identically -- i.e. the gate was structurally unable to see a
 * double-free, which is the failure mode of the whole #238/#250/#256/#277 family.
 * ST_DEAD also keeps the probe chain intact exactly as the tombstone did. */
#define ST_EMPTY 0u
#define ST_LIVE  1u
#define ST_DEAD  2u

/* tab_remove outcomes. */
#define RM_UNTRACKED   0
#define RM_FREED       1
#define RM_DOUBLE_FREE 2

static struct { uintptr_t key; size_t size; unsigned char state; } g_tab[TAB_CAP];
static long g_live_bytes;
static long g_live_blocks;
static long g_double_frees;
static atomic_flag g_lock = ATOMIC_FLAG_INIT;

static void lock(void)   { while (atomic_flag_test_and_set_explicit(&g_lock, memory_order_acquire)) {} }
static void unlock(void) { atomic_flag_clear_explicit(&g_lock, memory_order_release); }

static size_t slot(uintptr_t key) {
    return (size_t)((key * 0x9E3779B97F4A7C15ULL) >> 48) & TAB_MASK;
}

static void tab_insert(uintptr_t key, size_t size) {
    lock();
    size_t i = slot(key);
    /* Prefer reclaiming this key's own ST_DEAD slot: the allocator commonly hands
     * back an address it just freed, and reviving that slot is what keeps a later
     * legitimate free from being misread as a double free. */
    for (unsigned n = 0; n < TAB_CAP; n++) {
        unsigned char st = g_tab[i].state;
        if (st == ST_EMPTY || st == ST_DEAD) {
            g_tab[i].key = key;
            g_tab[i].size = size;
            g_tab[i].state = ST_LIVE;
            g_live_bytes += (long)size;
            g_live_blocks += 1;
            break;
        }
        i = (i + 1) & TAB_MASK;
    }
    unlock();
}

/* Classify a free of `key`: untracked, a normal first free, or a double free.
 * On RM_FREED the block is debited and *outsize set. On RM_DOUBLE_FREE nothing is
 * debited -- the block was already debited by the first free, so the balance stays
 * correct and the caller reports the event separately. */
static int tab_remove(uintptr_t key, size_t *outsize) {
    int outcome = RM_UNTRACKED;
    lock();
    size_t i = slot(key);
    for (unsigned n = 0; n < TAB_CAP; n++) {
        unsigned char st = g_tab[i].state;
        if (st == ST_EMPTY) break;          /* never used: probe chain ends here */
        if (g_tab[i].key == key) {
            if (st == ST_DEAD) {
                outcome = RM_DOUBLE_FREE;
            } else {
                if (outsize) *outsize = g_tab[i].size;
                g_live_bytes -= (long)g_tab[i].size;
                g_live_blocks -= 1;
                g_tab[i].state = ST_DEAD;   /* key retained on purpose */
                outcome = RM_FREED;
            }
            break;
        }
        i = (i + 1) & TAB_MASK;
    }
    unlock();
    return outcome;
}

/* Async-signal-safe stderr write: no malloc, no stdio buffering. */
static void emit(const char *s, size_t n) {
    ssize_t w = write(2, s, n);
    (void)w;
}

/* ---- real allocator resolution ------------------------------------------- */
#if defined(__APPLE__)
/* Under DYLD_INTERPOSE dyld does NOT redirect this image's own calls to the
 * interposed symbols, so we call the real allocators directly. */
static void init_reals(void) {
    real_malloc = malloc; real_calloc = calloc;
    real_realloc = realloc; real_free = free;
    g_ready = 1;
}
#else
/* Linux LD_PRELOAD: resolve reals lazily. dlsym() may call calloc before the
 * reals are ready, so serve those few allocations from a static bootstrap
 * arena. */
static int g_initing;
static char g_boot[1 << 16];
static size_t g_boot_used;
static int in_boot(const void *p) {
    return (const char *)p >= g_boot && (const char *)p < g_boot + sizeof(g_boot);
}
static void *boot_alloc(size_t n) {
    size_t a = (n + 15u) & ~((size_t)15);
    if (g_boot_used + a > sizeof(g_boot)) return NULL;
    void *p = g_boot + g_boot_used;
    g_boot_used += a;
    return p;
}
static void init_reals(void) {
    if (g_ready || g_initing) return;
    g_initing = 1;
    real_malloc  = (void *(*)(size_t))       dlsym(RTLD_NEXT, "malloc");
    real_calloc  = (void *(*)(size_t, size_t))dlsym(RTLD_NEXT, "calloc");
    real_realloc = (void *(*)(void *, size_t))dlsym(RTLD_NEXT, "realloc");
    real_free    = (void  (*)(void *))        dlsym(RTLD_NEXT, "free");
    g_initing = 0;
    g_ready = real_malloc && real_calloc && real_realloc && real_free;
}
#endif

static int counted_caller(void *caller) {
    uintptr_t a = (uintptr_t)caller;
    return g_text_lo && a >= g_text_lo && a < g_text_hi;
}

/* ---- the main-executable text range, resolved once before main() --------- */
#if defined(__APPLE__)
__attribute__((constructor)) static void init_range(void) {
    /* With DYLD_INSERT_LIBRARIES the main executable is NOT image 0, so find the
     * one MH_EXECUTE image explicitly. */
    uint32_t count = _dyld_image_count();
    for (uint32_t i = 0; i < count; i++) {
        const struct mach_header *mh = _dyld_get_image_header(i);
        if (!mh || mh->filetype != MH_EXECUTE) continue;
        unsigned long sz = 0;
        uint8_t *text = getsegmentdata((const struct mach_header_64 *)mh, "__TEXT", &sz);
        if (text && sz) {
            g_text_lo = (uintptr_t)text;
            g_text_hi = g_text_lo + sz;
        }
        break;
    }
}
#else
static int phdr_cb(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size; (void)data;
    if (info->dlpi_name && info->dlpi_name[0] != '\0') return 0; /* not main exe */
    for (int i = 0; i < info->dlpi_phnum; i++) {
        const ElfW(Phdr) *ph = &info->dlpi_phdr[i];
        if (ph->p_type == PT_LOAD && (ph->p_flags & PF_X)) {
            uintptr_t lo = (uintptr_t)info->dlpi_addr + ph->p_vaddr;
            uintptr_t hi = lo + ph->p_memsz;
            if (!g_text_lo || lo < g_text_lo) g_text_lo = lo;
            if (hi > g_text_hi) g_text_hi = hi;
        }
    }
    return 1; /* stop after the main program */
}
__attribute__((constructor)) static void init_range(void) {
    dl_iterate_phdr(phdr_cb, NULL);
}
#endif

/* ---- the interposed entry points ----------------------------------------- */
#if defined(__APPLE__)
#define WRAP(name) sushi_##name
#else
#define WRAP(name) name
#endif

void *WRAP(malloc)(size_t n) {
    void *caller = __builtin_return_address(0);
    if (!g_ready) {
        init_reals();
#if !defined(__APPLE__)
        if (!g_ready) return boot_alloc(n);
#endif
    }
    void *p = real_malloc(n);
    if (p && counted_caller(caller)) tab_insert((uintptr_t)p, n);
    return p;
}

void *WRAP(calloc)(size_t cnt, size_t sz) {
    void *caller = __builtin_return_address(0);
    if (!g_ready) {
        init_reals();
#if !defined(__APPLE__)
        if (!g_ready) {
            size_t n = cnt * sz;
            void *b = boot_alloc(n);
            if (b) memset(b, 0, n);
            return b;
        }
#endif
    }
    void *p = real_calloc(cnt, sz);
    if (p && counted_caller(caller)) tab_insert((uintptr_t)p, cnt * sz);
    return p;
}

void *WRAP(realloc)(void *p, size_t n) {
    void *caller = __builtin_return_address(0);
    if (!g_ready) init_reals();
#if !defined(__APPLE__)
    if (in_boot(p)) {
        void *np = WRAP(malloc)(n);
        if (np && p) {
            size_t avail = (size_t)((g_boot + sizeof(g_boot)) - (char *)p);
            memcpy(np, p, n < avail ? n : avail);
        }
        return np;
    }
#endif
    size_t oldsize = 0;
    int rm = p ? tab_remove((uintptr_t)p, &oldsize) : RM_UNTRACKED;
    if (rm == RM_DOUBLE_FREE) {
        /* realloc of an already-freed block: same defect class as a double free,
         * counted the same way. Nothing was debited, so the balance is unchanged. */
        lock();
        g_double_frees += 1;
        unlock();
        emit("SUSHI_LEAKCHECK: DOUBLE_FREE (realloc of freed block)\n", 54);
    }
    int had = (rm == RM_FREED);
    void *np = real_realloc(p, n);
    if (!np) {
        if (had) tab_insert((uintptr_t)p, oldsize); /* realloc failed; keep old */
        return NULL;
    }
    if (counted_caller(caller)) tab_insert((uintptr_t)np, n);
    return np;
}

void WRAP(free)(void *p) {
    if (!p) return;
#if !defined(__APPLE__)
    if (in_boot(p)) return; /* bootstrap arena: never handed to the real free */
#endif
    if (!g_ready) init_reals();
    if (tab_remove((uintptr_t)p, NULL) == RM_DOUBLE_FREE) {
        lock();
        g_double_frees += 1;
        unlock();
        emit("SUSHI_LEAKCHECK: DOUBLE_FREE\n", 29);
        /* Deliberately WITHHOLD the pointer from the real allocator. Handing it
         * over aborts the process, which kills the destructor before it can report
         * -- the runner then sees no report line and scores the run as a SKIP, so
         * the most severe defect the gate can encounter used to be the one it was
         * least able to report. Withholding costs one leaked block in an already
         * broken program and buys an attributable failure. */
        return;
    }
    real_free(p);
}

#if defined(__APPLE__)
#define DYLD_INTERPOSE(_repl, _orig)                                            \
    __attribute__((used)) static struct {                                      \
        const void *repl;                                                      \
        const void *orig;                                                      \
    } _interpose_##_orig __attribute__((section("__DATA,__interpose"))) = {     \
        (const void *)(uintptr_t)&_repl, (const void *)(uintptr_t)&_orig }
DYLD_INTERPOSE(sushi_malloc, malloc);
DYLD_INTERPOSE(sushi_calloc, calloc);
DYLD_INTERPOSE(sushi_realloc, realloc);
DYLD_INTERPOSE(sushi_free, free);
#endif

__attribute__((destructor)) static void report(void) {
    lock();
    long bytes = g_live_bytes;
    long blocks = g_live_blocks;
    long doubles = g_double_frees;
    unlock();
    /* A negative balance means more was debited than credited -- an over-free the
     * double-free detector did not attribute (e.g. a free of an interior pointer,
     * or a tracked block whose insert lost the table race). It is reported, not
     * clamped away: clamping is what let over-freeing masquerade as a clean run.
     * `leaked` keeps its non-negative meaning so existing consumers are unaffected. */
    long over = 0;
    if (bytes < 0) { over = -bytes; bytes = 0; }
    if (blocks < 0) blocks = 0;
    char buf[160];
    int n = snprintf(buf, sizeof(buf),
                     "SUSHI_LEAKCHECK: leaked=%ld blocks=%ld double_frees=%ld over_freed=%ld\n",
                     bytes, blocks, doubles, over);
    if (n > 0) emit(buf, (size_t)n); /* stderr; keep stdout pristine */
}
