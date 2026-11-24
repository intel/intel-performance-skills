/* residuals.c - least-squares fit error, called once per optimizer step.
 * Built with: gcc -O2 -g -march=native -c residuals.c
 */
#include <stddef.h>

double sse(const double *obs, const double *pred, size_t n)
{
    double s = 0.0;
    for (size_t i = 0; i < n; i++) {
        double r = obs[i] - pred[i];
        s += r * r;
    }
    return s;
}
