/* Drives the interposer's side table DIRECTLY, with keys we choose (issue #371).
 *
 * The table's defect is a collision between two addresses, and a C program cannot ask
 * the allocator for two addresses that collide. It does not have to: `slot()` is a pure
 * function of the key, so a colliding pair can be searched for. That turns "an unlucky
 * pair of addresses in one run" into an assertion that holds every run.
 *
 * Including the .c file is deliberate -- `tab_insert`, `tab_remove` and `tab_retire` are
 * static, and the alternative is exporting the table's internals for a test.
 */
#include "leakcheck.c"

#include <stdio.h>

static int g_fails;

static void check(const char *what, int got, int want) {
    if (got == want) {
        printf("ok %s\n", what);
    } else {
        printf("FAIL %s got=%d want=%d\n", what, got, want);
        g_fails++;
    }
}

/* Two DISTINCT keys on the same slot. Aligned like a heap pointer; the search is over a
 * fixed range, so it either finds a pair every run or none ever. */
static int find_colliding_pair(uintptr_t base, uintptr_t *a, uintptr_t *b) {
    for (uintptr_t first = base; first < base + 0x10000; first += 16) {
        for (uintptr_t k = first + 16; k < first + 0x400000; k += 16) {
            if (slot(k) == slot(first)) { *a = first; *b = k; return 1; }
        }
    }
    return 0;
}

int main(void) {
    uintptr_t a = 0, b = 0;
    if (!find_colliding_pair(0x1000, &a, &b)) {
        printf("FAIL no_colliding_pair\n");
        return 2;
    }
    printf("pair a=%zu b=%zu slot=%zu\n", (size_t)a, (size_t)b, slot(a));

    /* #371: an insert that collides must not consume ANOTHER key's tombstone. */
    tab_insert(a, 8);
    check("first_free_is_normal", tab_remove(a, NULL), RM_FREED);
    tab_insert(b, 8);
    check("double_free_survives_a_colliding_insert", tab_remove(a, NULL), RM_DOUBLE_FREE);

    /* #359's property: the SAME address handed out again revives its own slot, so the
     * next legitimate free is not reported as a double free. */
    uintptr_t c = 0x900010;
    tab_insert(c, 8);
    check("first_free_of_c", tab_remove(c, NULL), RM_FREED);
    tab_insert(c, 8);
    check("reissued_address_frees_normally", tab_remove(c, NULL), RM_FREED);

    /* tab_retire: an UNTRACKED allocation at a dead address drops the retained key. */
    uintptr_t d = 0x900020;
    tab_insert(d, 8);
    tab_remove(d, NULL);
    tab_retire(d);
    check("retired_tombstone_stops_matching", tab_remove(d, NULL), RM_UNTRACKED);

    /* A key whose own tombstone sits LATER on the chain than a foreign one still finds
     * its own, so the revival above cannot be defeated by an unlucky probe order. */
    uintptr_t e = 0, f = 0;
    if (find_colliding_pair(0x2000000, &e, &f)) {
        tab_insert(e, 8);
        tab_insert(f, 8);
        tab_remove(e, NULL);
        tab_remove(f, NULL);
        tab_insert(f, 8);   /* f's own slot is the SECOND on the chain */
        check("own_slot_wins_over_an_earlier_foreign_one",
              tab_remove(f, NULL), RM_FREED);
        check("the_foreign_tombstone_was_left_alone",
              tab_remove(e, NULL), RM_DOUBLE_FREE);
    }

    printf("fails=%d\n", g_fails);
    return g_fails != 0;
}
