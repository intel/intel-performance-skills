// The baseline the speed gate is measured against: std::sort, as shipped.
#include <algorithm>
#include <cstddef>
void sort_scores(float *scores, size_t n) { std::sort(scores, scores + n); }
