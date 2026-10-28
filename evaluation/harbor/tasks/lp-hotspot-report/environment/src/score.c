/* Scoring kernels for solver. */
#include <stddef.h>
#include "solver.h"

double normalize_row(double *row, size_t n);

/*
 * Similarity score between a candidate row and the reference row. Called once
 * per candidate per block; n is the feature count (4096 for case1).
 */

double accumulate_scores(const double *a, const double *b, size_t n)
{
    double sum = 0.0;
    for (size_t i = 0; i < n; ++i)
        sum += a[i] * b[i];

    return sum;
}
