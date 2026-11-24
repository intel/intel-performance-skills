#ifndef CONFIG_H
#define CONFIG_H

#define MAX_BACKENDS 64

struct backend {
    char  host[128];
    int   port;
    float weight;      /* normalised: all weights sum to 1 */
};

struct config {
    int            listen_port;
    int            worker_threads;
    char           log_path[256];
    int            nbackends;
    struct backend backends[MAX_BACKENDS];
};

int config_load(const char *path, struct config *cfg);

#endif
