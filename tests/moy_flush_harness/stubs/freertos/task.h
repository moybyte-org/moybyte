#ifndef H_STUB_FREERTOS_TASK_H
#define H_STUB_FREERTOS_TASK_H

#include "freertos/FreeRTOS.h"

typedef void *TaskHandle_t;
typedef void (*TaskFunction_t)(void *);

uint32_t ulTaskNotifyTake(BaseType_t xClearCountOnExit, TickType_t xTicksToWait);
void vTaskNotifyGiveFromISR(TaskHandle_t xTaskToNotify,
                            BaseType_t *pxHigherPriorityTaskWoken);
void vTaskDelete(TaskHandle_t xTaskToDelete);

#endif // H_STUB_FREERTOS_TASK_H
