#ifndef KVGATE_PIPELINE_H
#define KVGATE_PIPELINE_H
#include <stdint.h>

/* Ingest pipeline counters, exported on /metrics. One parser thread and one
 * flusher thread update them; the metrics thread reads them once a second. */
struct pipeline_stats {
    uint64_t parsed;        /* parser thread  */
    uint64_t parse_errors;  /* parser thread  */
    uint64_t flushed;       /* flusher thread */
    uint64_t flush_bytes;   /* flusher thread */
};

extern struct pipeline_stats g_pipe_stats;

#endif
