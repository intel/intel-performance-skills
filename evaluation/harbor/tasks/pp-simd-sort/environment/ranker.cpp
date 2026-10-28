// Score sorting for the ranking pipeline.
#include <algorithm>
#include "ranker.h"

void sort_scores(float *scores, size_t n) {
    std::sort(scores, scores + n);
}
