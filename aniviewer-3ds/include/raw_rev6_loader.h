#ifndef RAW_REV6_LOADER_H
#define RAW_REV6_LOADER_H

#include <stdbool.h>
#include <stddef.h>

#include "av6_runtime.h"

#ifdef __cplusplus
extern "C" {
#endif

bool raw_rev6_load_from_bin_with_animation(
    const char *bin_path,
    uint32_t animation_index,
    Av6Package *out_pkg,
    uint32_t *out_animation_count,
    uint32_t *out_selected_animation_index,
    char *out_animation_name,
    size_t out_animation_name_size,
    char *err,
    size_t err_size
);

bool raw_rev6_load_from_bin(const char *bin_path, Av6Package *out_pkg, char *err, size_t err_size);

#ifdef __cplusplus
}
#endif

#endif
