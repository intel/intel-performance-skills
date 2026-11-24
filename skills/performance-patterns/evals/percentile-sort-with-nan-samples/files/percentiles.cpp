// percentiles.cpp - per-endpoint latency report
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <vector>

struct Percentiles {
    float p50, p90, p99, p999;
    std::size_t failed;   // requests that never completed (recorded as NaN)
};

// samples: per-request latency in milliseconds; NaN marks a failed request.
// Called once a minute per endpoint, with 5-20 million samples each time.
Percentiles compute_percentiles(std::vector<float> samples)
{
    std::sort(samples.begin(), samples.end());   // NaNs end up at the back

    std::size_t failed = std::count_if(samples.begin(), samples.end(),
                                       [](float x) { return std::isnan(x); });
    std::size_t ok = samples.size() - failed;
    if (ok == 0)
        return {NAN, NAN, NAN, NAN, failed};

    auto at = [&](double q) {
        return samples[static_cast<std::size_t>(q * (ok - 1))];
    };
    return {at(0.50), at(0.90), at(0.99), at(0.999), failed};
}
