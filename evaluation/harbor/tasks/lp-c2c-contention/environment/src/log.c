/* Logging. Excerpt. */
int g_log_level __attribute__((aligned(64)));

int log_enabled(int level)
{
    return level <= g_log_level;
}
