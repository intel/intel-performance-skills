// Correctness: sort_scores() against std::sort, element-for-element (==, so the
// relative order of -0.0 and +0.0 is free), on every length 0..600, random lengths
// up to 3M, and adversarial shapes: sorted, reversed, all-equal, few distinct values,
// +-0, infinities, denormals, extreme magnitudes; at unaligned starting addresses.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <vector>
void sort_scores(float *scores, size_t n);

static uint64_t s = 0x9E3779B97F4A7C15ull;
static uint64_t rnd() { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }
static int bad = 0;

static void one(std::vector<float> in, const char *what, size_t off) {
    std::vector<float> ref = in, buf(in.size() + off + 1);
    std::sort(ref.begin(), ref.end());
    std::copy(in.begin(), in.end(), buf.begin() + off);
    sort_scores(buf.data() + off, in.size());
    for (size_t i = 0; i < in.size(); i++)
        if (!(buf[off + i] == ref[i])) {
            if (bad++ < 5) printf("FAIL %s n=%zu off=%zu at %zu: %g vs %g\n", what, in.size(), off, i,
                                  (double)buf[off + i], (double)ref[i]);
            return;
        }
}

static float gen(int kind) {
    const float inf = std::numeric_limits<float>::infinity();
    switch (kind) {
    case 0: return (float)((int64_t)(rnd() >> 11) - (1ll << 52)) / (float)(1ll << 40);
    case 1: return (float)(rnd() % 5) - 2.0f;
    case 2: { float v[] = {0.0f, -0.0f, inf, -inf, 1e-42f, -1e-42f, 3.4e38f, -3.4e38f, 1.0f};
              return v[rnd() % 9]; }
    default: { uint32_t u = (uint32_t)rnd(); float f; __builtin_memcpy(&f, &u, 4);
               return std::isnan(f) ? 0.5f : f; }
    }
}

int main() {
    for (size_t n = 0; n <= 600 && bad < 5; n++)
        for (int kind = 0; kind < 4; kind++) {
            std::vector<float> v(n);
            for (auto &x : v) x = gen(kind);
            one(v, "small", n % 5);
        }
    size_t lens[] = {1000, 4097, 65536 + 3, 1000000, 3000017};
    for (size_t n : lens)
        for (int kind = 0; kind < 4 && bad < 5; kind++) {
            std::vector<float> v(n);
            for (auto &x : v) x = gen(kind);
            one(v, "random", 1);
            std::sort(v.begin(), v.end());
            one(v, "sorted", 2);
            std::reverse(v.begin(), v.end());
            one(v, "reversed", 3);
        }
    one(std::vector<float>(100000, 7.25f), "all-equal", 1);
    puts(bad ? "CHECK FAIL" : "CHECK OK");
    return bad != 0;
}
