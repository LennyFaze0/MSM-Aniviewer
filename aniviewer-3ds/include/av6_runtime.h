#ifndef AV6_RUNTIME_H
#define AV6_RUNTIME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    AV6_NO_SPRITE = 0xFFFF,
};

typedef struct {
    float x;
    float y;
    float w;
    float h;
    float frame_x;
    float frame_y;
    float frame_w;
    float frame_h;
    float draw_scale;
    uint16_t sheet_index;
    uint8_t rotated;
    char *name;
} Av6Sprite;

typedef struct {
    int32_t id;
    int32_t parent_id;
    uint32_t blend;
    uint32_t first_keyframe;
    uint32_t keyframe_count;
    float anchor_x;
    float anchor_y;
    char *name;
} Av6Layer;

typedef struct {
    uint32_t layer_index;
    float time;
    float x;
    float y;
    float scale_x;
    float scale_y;
    float rotation;
    float opacity;
    uint8_t r;
    uint8_t g;
    uint8_t b;
    uint8_t a;
    uint16_t sprite_index;
    int8_t immediate_pos;
    int8_t immediate_scale;
    int8_t immediate_rotation;
    int8_t immediate_opacity;
    int8_t immediate_sprite;
    int8_t immediate_rgb;
} Av6Keyframe;

typedef struct {
    uint32_t version;
    uint32_t sprite_count;
    uint32_t layer_count;
    uint32_t keyframe_count;
    float duration;
    float anim_width;
    float anim_height;
    float loop_offset;
    uint8_t centered;
    uint32_t texture_blob_size;
    uint8_t *texture_blob;
    char *sheet_name;
    uint32_t external_sheet_count;
    char **external_sheet_paths;
    Av6Sprite *sprites;
    Av6Layer *layers;
    Av6Keyframe *keyframes;
} Av6Package;

typedef struct {
    bool valid;
    float x;
    float y;
    float scale_x;
    float scale_y;
    float rotation;
    float opacity;
    uint8_t r;
    uint8_t g;
    uint8_t b;
    uint8_t a;
    uint16_t sprite_index;
} Av6Pose;

bool av6_load_package(const char *path, Av6Package *out_pkg, char *err, size_t err_size);
void av6_free_package(Av6Package *pkg);
Av6Pose av6_eval_layer_pose(const Av6Package *pkg, uint32_t layer_index, float time_sec);

#ifdef __cplusplus
}
#endif

#endif
