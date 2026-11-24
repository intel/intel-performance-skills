/* config.c - service configuration loader.
 *
 * config_load() is called once from main() at startup.  Typical config files
 * are 20-100 lines with up to MAX_BACKENDS backend entries.
 */
#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "config.h"

static char *trim(char *s)
{
    while (isspace((unsigned char)*s))
        s++;
    char *end = s + strlen(s);
    while (end > s && isspace((unsigned char)end[-1]))
        *--end = '\0';
    return s;
}

static void config_error(const char *path, int lineno, const char *msg)
{
    fprintf(stderr, "%s:%d: %s\n", path, lineno, msg);
}

static int parse_backend(char *val, struct backend *b)
{
    /* "host:port weight", e.g. "10.0.0.12:8080 3" */
    char *colon = strchr(val, ':');
    if (!colon)
        return -1;
    *colon = '\0';
    snprintf(b->host, sizeof b->host, "%s", val);

    char *end;
    long port = strtol(colon + 1, &end, 10);
    if (port <= 0 || port > 65535)
        return -1;
    b->port = (int)port;

    b->weight = 1.0f;
    if (*end != '\0') {
        b->weight = strtof(end, &end);
        if (b->weight <= 0.0f)
            return -1;
    }
    return 0;
}

int config_load(const char *path, struct config *cfg)
{
    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "config: cannot open %s: %s\n", path, strerror(errno));
        return -1;
    }

    memset(cfg, 0, sizeof *cfg);
    cfg->listen_port = 8080;
    cfg->worker_threads = 4;

    char line[512];
    int lineno = 0;
    while (fgets(line, sizeof line, f)) {
        lineno++;
        char *s = trim(line);
        if (*s == '\0' || *s == '#')
            continue;

        char *eq = strchr(s, '=');
        if (!eq) {
            config_error(path, lineno, "expected key = value");
            goto fail;
        }
        *eq = '\0';
        char *key = trim(s);
        char *val = trim(eq + 1);

        if (strcmp(key, "listen_port") == 0) {
            cfg->listen_port = atoi(val);
        } else if (strcmp(key, "worker_threads") == 0) {
            cfg->worker_threads = atoi(val);
        } else if (strcmp(key, "log_path") == 0) {
            snprintf(cfg->log_path, sizeof cfg->log_path, "%s", val);
        } else if (strcmp(key, "backend") == 0) {
            if (cfg->nbackends == MAX_BACKENDS) {
                config_error(path, lineno, "too many backends");
                goto fail;
            }
            if (parse_backend(val, &cfg->backends[cfg->nbackends]) != 0) {
                config_error(path, lineno, "bad backend, expected host:port [weight]");
                goto fail;
            }
            cfg->nbackends++;
        } else {
            config_error(path, lineno, "unknown key (ignored)");
        }
    }
    fclose(f);

    if (cfg->nbackends == 0) {
        fprintf(stderr, "config: no backends configured\n");
        return -1;
    }

    /* normalise backend weights so they sum to 1 */
    float total = 0.0f;
    for (int i = 0; i < cfg->nbackends; i++)
        total += cfg->backends[i].weight;
    for (int i = 0; i < cfg->nbackends; i++)
        cfg->backends[i].weight /= total;

    return 0;

fail:
    fclose(f);
    return -1;
}
