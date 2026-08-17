/* Stands in for a system library: these call sites are OUTSIDE the main executable, so
 * `counted_caller` is false for them and the interposer tracks nothing they allocate. */
#include <stdlib.h>

void *alien_alloc(unsigned long n) { return malloc(n); }
void alien_free(void *p) { free(p); }
