#ifndef H_STUB_FREERTOS_IDF_ADDITIONS_H
#define H_STUB_FREERTOS_IDF_ADDITIONS_H

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

BaseType_t xTaskCreatePinnedToCore(TaskFunction_t pxTaskCode,
                                   const char *const pcName,
                                   const uint32_t usStackDepth,
                                   void *const pvParameters,
                                   UBaseType_t uxPriority,
                                   TaskHandle_t *const pxCreatedTask,
                                   const BaseType_t xCoreID);

#endif // H_STUB_FREERTOS_IDF_ADDITIONS_H
