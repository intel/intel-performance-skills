/* pktsrv.c - packet processing server: N worker threads pull packets from
 * per-worker RX rings, process them and account for them.
 */
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "ring.h"
#include "stats.h"

struct worker_stats g_wstats[MAX_WORKERS];
struct server_state g_state = { .log_level = 1 };

struct pkt {
    uint32_t len;
    uint8_t  data[2048];
};

struct config {
    int batch;
    int checksum_offload;
    int reload_seq;
} g_cfg = { .batch = 32 };

static void process_packet(struct pkt *p)
{
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < p->len; i++)
        h = (h ^ p->data[i]) * 16777619u;
    if (!g_cfg.checksum_offload)
        p->data[0] ^= (uint8_t)h;
}

static void on_sigterm(int sig)
{
    (void)sig;
    g_state.shutting_down = 1;
}

static void *worker_main(void *arg)
{
    int id = (int)(intptr_t)arg;
    struct worker_stats *ws = &g_wstats[id];
    struct ring *rx = ring_for_worker(id);
    struct pkt p;

    while (!g_state.shutting_down) {
        if (!ring_pop(rx, &p, sizeof p)) {
            __builtin_ia32_pause();
            continue;
        }
        atomic_fetch_add_explicit(&g_state.inflight, 1, memory_order_relaxed);
        process_packet(&p);
        ws->rx_packets++;
        ws->rx_bytes += p.len;
        atomic_fetch_sub_explicit(&g_state.inflight, 1, memory_order_relaxed);
    }
    return NULL;
}

static void *monitor_main(void *arg)
{
    int nworkers = (int)(intptr_t)arg;
    uint64_t last = 0;

    while (!g_state.shutting_down) {
        sleep(1);
        uint64_t pkts = 0, bytes = 0;
        for (int i = 0; i < nworkers; i++) {
            pkts  += g_wstats[i].rx_packets;
            bytes += g_wstats[i].rx_bytes;
        }
        fprintf(stderr, "pkts/s=%llu bytes=%llu inflight=%llu\n",
                (unsigned long long)(pkts - last), (unsigned long long)bytes,
                (unsigned long long)atomic_load(&g_state.inflight));
        last = pkts;
    }
    return NULL;
}

int main(int argc, char **argv)
{
    int nworkers = 8;
    int duration = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--threads") && i + 1 < argc)
            nworkers = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--duration") && i + 1 < argc)
            duration = atoi(argv[++i]);
    }
    if (nworkers < 1 || nworkers > MAX_WORKERS) {
        fprintf(stderr, "--threads must be 1..%d\n", MAX_WORKERS);
        return 1;
    }

    signal(SIGTERM, on_sigterm);
    rings_init(nworkers);

    pthread_t workers[MAX_WORKERS], mon;
    for (int i = 0; i < nworkers; i++)
        pthread_create(&workers[i], NULL, worker_main, (void *)(intptr_t)i);
    pthread_create(&mon, NULL, monitor_main, (void *)(intptr_t)nworkers);

    if (duration > 0) {
        sleep(duration);
        g_state.shutting_down = 1;
    }
    for (int i = 0; i < nworkers; i++)
        pthread_join(workers[i], NULL);
    pthread_join(mon, NULL);
    return 0;
}
