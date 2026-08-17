/* The control for issue #359: a genuine double free, both calls from the main executable,
 * must still be reported. */
#include <stdlib.h>

int main(void) {
    void *p = malloc(64);
    free(p);
    free(p);
    return 0;
}
