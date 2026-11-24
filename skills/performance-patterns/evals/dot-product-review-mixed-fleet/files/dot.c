/* dot.c - part of libvecmath */
#include <stddef.h>

float dot(const float *a, const float *b, int n)
{
    float sum = 0.0f;
    for (int i = 0; i < n; i++)
        sum += a[i] * b[i];
    return sum;
}
