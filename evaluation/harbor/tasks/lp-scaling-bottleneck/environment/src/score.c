/* Per-record model evaluation (gradient-boosted trees). */
#include "ingestd.h"

float score_block(const struct parsed *p, const struct model *m)
{
    float acc = m->bias;
    for (int t = 0; t < m->ntrees; t++) {
        const struct node *n = m->trees[t];
        while (n->feature >= 0)
            n = p->feat[n->feature] < n->threshold ? n->left : n->right;
        acc += n->value;
    }
    return acc;
}
