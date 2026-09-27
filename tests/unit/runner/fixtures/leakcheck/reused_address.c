/* Issue #359: a tombstone must not outlive the reuse of its address.
 *
 * The program frees X, so X's slot goes ST_DEAD with the key retained. A library then
 * allocates -- untracked -- and the allocator hands back the very same X. The library
 * frees its own block, the retained key matches, and the gate reported a double free that
 * no code committed. */
#include <stdio.h>
#include <stdlib.h>

void *alien_alloc(unsigned long n);
void alien_free(void *p);

int main(void) {
    void *x = malloc(64);
    free(x);

    void *y = alien_alloc(64);
    alien_free(y);

    /* The reproduction depends on the allocator reusing the address. Say so, rather than
     * let a non-reusing allocator make this test pass for the wrong reason. */
    printf("reused=%d\n", x == y);
    return 0;
}
