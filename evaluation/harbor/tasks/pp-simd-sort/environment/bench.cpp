#include <chrono>
#include <cstdio>
#include <cstdint>
#include <vector>
#include "ranker.h"

int main() {
    size_t n = 1000000;
    std::vector<float> base(n), v;
    uint64_t s = 0x9E3779B97F4A7C15ull;
    for (auto &x : base) {          /* roughly normal scores: sum of 4 uniforms */
        float acc = 0;
        for (int k = 0; k < 4; k++) {
            s ^= s << 13; s ^= s >> 7; s ^= s << 17;
            acc += (float)(s >> 40) / (float)(1 << 24);
        }
        x = (acc - 2.0f) * 50.0f;
    }
    double best = 1e9;
    for (int r = 0; r < 5; r++) {
        v = base;
        auto t0 = std::chrono::steady_clock::now();
        sort_scores(v.data(), n);
        double t = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        if (t < best) best = t;
    }
    bool ok = true;
    for (size_t i = 1; i < n; i++) ok &= !(v[i] < v[i - 1]);
    printf("%s  %.1f Melem/s\n", ok ? "sorted" : "NOT SORTED", n / best / 1e6);
    return 0;
}
