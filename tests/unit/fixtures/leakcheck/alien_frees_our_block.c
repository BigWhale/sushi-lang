/* Also a genuine double free, and the reason the fix does not simply suppress a report
 * whose caller sits outside the main executable: the block IS ours, the library never
 * allocated it, and the second free is real. */
#include <stdlib.h>

void alien_free(void *p);

int main(void) {
    void *p = malloc(64);
    free(p);
    alien_free(p);
    return 0;
}
