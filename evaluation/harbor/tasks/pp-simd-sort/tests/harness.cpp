// Throughput of sort_scores() on 1M roughly-normal float scores (the production
// shape), best of 7. Prints "<Melem/s> <process CPU time / wall time>": the second
// number exposes extra threads, which the task forbids.
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <sys/resource.h>
#include <vector>
void sort_scores(float *scores, size_t n);
static double cpu_s() {
    rusage u;
    getrusage(RUSAGE_SELF, &u);
    return u.ru_utime.tv_sec + u.ru_stime.tv_sec + (u.ru_utime.tv_usec + u.ru_stime.tv_usec) * 1e-6;
}
int main() {
    size_t n = 1000000;
    std::vector<float> base(n), v(n);
    uint64_t s = 0x2545F4914F6CDD1Dull;
    for (auto &x : base) {
        float acc = 0;
        for (int k = 0; k < 4; k++) {
            s ^= s << 13; s ^= s >> 7; s ^= s << 17;
            acc += (float)(s >> 40) / (float)(1 << 24);
        }
        x = (acc - 2.0f) * 50.0f;
    }
    double best = 1e9, wall = 0, cpu = 0;
    for (int r = 0; r < 7; r++) {
        v = base;
        double c0 = cpu_s();
        auto t0 = std::chrono::steady_clock::now();
        sort_scores(v.data(), n);
        double t = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        cpu += cpu_s() - c0;
        wall += t;
        if (t < best) best = t;
    }
    volatile float sink = v[n / 2];
    (void)sink;
    printf("%.3f %.3f\n", n / best / 1e6, cpu / wall);
    return 0;
}
