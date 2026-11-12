// moy_tasks: the table's rows and the one spawn (moy_tasks.h).

#include "moy_tasks.h"

#ifdef MOY_ON_BOARD
#include "freertos/idf_additions.h"

static const moy_task_row_t s_rows[MOY_TASK_COUNT] = {
    #define MOY_TASK_ROW(id, name, stack, prio, core, psram) {name, stack, prio, core, psram},
    MOY_TASKS(MOY_TASK_ROW)
    #undef MOY_TASK_ROW
};

const moy_task_row_t *moy_task_row(moy_task_id_t id) {
    return &s_rows[id];
}

static int resolve(int row, int given, int caller) {
    return row == MOY_TASK_GIVEN ? given : row == MOY_TASK_CALLER ? caller : row;
}

bool moy_task_spawn_as(moy_task_id_t id, const char *name, uint32_t stack, int prio, int core,
                       TaskFunction_t fn, void *arg, TaskHandle_t *out) {
    const moy_task_row_t *r = &s_rows[id];
    const char *n = r->name != NULL ? r->name : name;
    uint32_t depth = r->stack == MOY_TASK_GIVEN ? stack : (uint32_t)r->stack;
    UBaseType_t p = (UBaseType_t)resolve(r->prio, prio, (int)uxTaskPriorityGet(NULL));
    BaseType_t c = (BaseType_t)resolve(r->core, core, (int)xPortGetCoreID());
    if (r->psram) {
        return xTaskCreatePinnedToCoreWithCaps(fn, n, depth, arg, p, out, c,
                                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) == pdPASS;
    }
    return xTaskCreatePinnedToCore(fn, n, depth, arg, p, out, c) == pdPASS;
}

bool moy_task_spawn(moy_task_id_t id, TaskFunction_t fn, void *arg, TaskHandle_t *out) {
    return moy_task_spawn_as(id, NULL, 0, 0, 0, fn, arg, out);
}

TaskHandle_t moy_task_spawn_static(moy_task_id_t id, TaskFunction_t fn, void *arg,
                                   StackType_t *stack, StaticTask_t *tcb) {
    const moy_task_row_t *r = &s_rows[id];
    return xTaskCreateStaticPinnedToCore(fn, r->name, (uint32_t)r->stack / sizeof(StackType_t), arg,
                                         (UBaseType_t)r->prio, stack, tcb, (BaseType_t)r->core);
}
#endif
