#include <3ds.h>
#include <citro2d.h>
#include <tex3ds.h>

#include <ctype.h>
#include <dirent.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "av6_runtime.h"
#include "raw_rev6_loader.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define TOP_WIDTH 400.0f
#define TOP_HEIGHT 240.0f
#define BOTTOM_WIDTH 320.0f
#define BOTTOM_HEIGHT 240.0f

#define RAW_BIN_SCAN_DIR "sdmc:/3ds/aniviewer3ds/raw"

typedef enum {
    PACKAGE_KIND_RAW_BIN = 0,
} PackageKind;

typedef struct {
    Tex3DS_SubTexture subtex;
    C2D_Image image;
} RuntimeSpriteImage;

typedef struct {
    uint8_t valid;
    float m00;
    float m01;
    float m10;
    float m11;
    float tx;
    float ty;
    float anchor_world_x;
    float anchor_world_y;
} RuntimeWorldTransform;

typedef struct {
    char **paths;
    char **names;
    uint8_t *kinds;
    size_t count;
    size_t index;
} PackageList;

typedef struct {
    Av6Package package;
    bool loaded;

    C2D_SpriteSheet *sprite_sheets;
    uint32_t sprite_sheet_count;
    RuntimeSpriteImage *runtime_images;
    bool textured_mode;

    int32_t *parent_index;
    Av6Pose *local_poses;
    Av6Pose *world_poses;
    RuntimeWorldTransform *world_xforms;
    uint8_t *world_state;
    float *depth_target;
    float *depth_smooth;
    bool depth_profile_ready;

    char load_error[256];
    char texture_status[256];
    uint32_t raw_anim_count;
    uint32_t raw_anim_index;
    char raw_anim_name[128];
} RuntimeState;

static void draw_text_line(C2D_TextBuf buf, C2D_Text *text, float x, float y, const char *line, u32 color) {
    C2D_TextBufClear(buf);
    C2D_TextParse(text, buf, line);
    C2D_TextOptimize(text);
    C2D_DrawText(text, C2D_AtBaseline | C2D_WithColor, x, y, 0.5f, 0.42f, 0.42f, color);
}

static float safe_value(float value, float fallback) {
    if (value > 0.001f) {
        return value;
    }
    return fallback;
}

static float clamp01(float value) {
    if (value < 0.0f) {
        return 0.0f;
    }
    if (value > 1.0f) {
        return 1.0f;
    }
    return value;
}

static float clampf_range(float value, float min_value, float max_value) {
    if (value < min_value) {
        return min_value;
    }
    if (value > max_value) {
        return max_value;
    }
    return value;
}

static void build_local_transform(const Av6Layer *layer, const Av6Pose *pose, RuntimeWorldTransform *out) {
    float sx;
    float sy;
    float angle;
    float cos_a;
    float sin_a;

    if (!out) {
        return;
    }

    memset(out, 0, sizeof(*out));

    if (!layer || !pose || !pose->valid) {
        return;
    }

    sx = pose->scale_x / 100.0f;
    sy = pose->scale_y / 100.0f;

    if (fabsf(sx) < 0.0001f) {
        sx = sx < 0.0f ? -0.0001f : 0.0001f;
    }
    if (fabsf(sy) < 0.0001f) {
        sy = sy < 0.0f ? -0.0001f : 0.0001f;
    }

    angle = pose->rotation * ((float)M_PI / 180.0f);
    cos_a = cosf(angle);
    sin_a = sinf(angle);

    out->valid = 1u;
    out->m00 = sx * cos_a;
    out->m01 = -sy * sin_a;
    out->m10 = sx * sin_a;
    out->m11 = sy * cos_a;
    out->tx = pose->x - (out->m00 * layer->anchor_x + out->m01 * layer->anchor_y);
    out->ty = pose->y - (out->m10 * layer->anchor_x + out->m11 * layer->anchor_y);
    out->anchor_world_x = pose->x;
    out->anchor_world_y = pose->y;
}

static void apply_world_view_transform(
    const RuntimeWorldTransform *xf,
    float origin_x,
    float origin_y,
    float fit_scale,
    float fit_offset_x,
    float fit_offset_y,
    float depth_offset_x,
    float depth_offset_y,
    float depth_scale
) {
    float sx;
    float sy;
    float angle;
    float cos_a;
    float sin_a;
    float det;
    float kxy;
    float shear_x;

    if (!xf || !xf->valid) {
        return;
    }

    if (!(depth_scale > 0.0001f) || !isfinite(depth_scale)) {
        depth_scale = 1.0f;
    }

    sx = sqrtf(xf->m00 * xf->m00 + xf->m10 * xf->m10);
    if (sx < 0.0001f) {
        sx = 0.0001f;
    }

    angle = atan2f(xf->m10, xf->m00);
    cos_a = cosf(angle);
    sin_a = sinf(angle);

    sy = (-sin_a * xf->m01) + (cos_a * xf->m11);
    det = xf->m00 * xf->m11 - xf->m01 * xf->m10;
    if (fabsf(sy) < 0.0001f) {
        sy = det < 0.0f ? -0.0001f : 0.0001f;
    }

    kxy = (cos_a * xf->m01) + (sin_a * xf->m11);
    shear_x = kxy / sy;

    C2D_ViewReset();
    C2D_ViewTranslate(
        fit_offset_x + fit_scale * (origin_x + xf->tx) + depth_offset_x,
        fit_offset_y + fit_scale * (origin_y + xf->ty) + depth_offset_y
    );
    C2D_ViewRotate(angle);
    C2D_ViewShear(shear_x, 0.0f);
    C2D_ViewScale(sx * fit_scale * depth_scale, sy * fit_scale * depth_scale);
}


static char *dup_text(const char *text) {
    size_t len;
    char *out;

    if (!text) {
        return NULL;
    }
    len = strlen(text);
    out = (char *)malloc(len + 1u);
    if (!out) {
        return NULL;
    }
    memcpy(out, text, len + 1u);
    return out;
}

static bool has_bin_extension(const char *name) {
    size_t len;
    const char *tail;

    if (!name) {
        return false;
    }
    len = strlen(name);
    if (len < 4u) {
        return false;
    }
    tail = name + len - 4u;
    return (
        tail[0] == '.'
        && tolower((unsigned char)tail[1]) == 'b'
        && tolower((unsigned char)tail[2]) == 'i'
        && tolower((unsigned char)tail[3]) == 'n'
    );
}

static void free_package_list(PackageList *list) {
    size_t i;

    if (!list) {
        return;
    }

    if (list->paths) {
        for (i = 0; i < list->count; i++) {
            free(list->paths[i]);
            list->paths[i] = NULL;
        }
        free(list->paths);
    }

    if (list->names) {
        for (i = 0; i < list->count; i++) {
            free(list->names[i]);
            list->names[i] = NULL;
        }
        free(list->names);
    }

    if (list->kinds) {
        free(list->kinds);
    }

    list->paths = NULL;
    list->names = NULL;
    list->kinds = NULL;
    list->count = 0;
    list->index = 0;
}

static bool append_package(PackageList *list, const char *path, const char *name, PackageKind kind) {
    char **new_paths;
    char **new_names;
    uint8_t *new_kinds;

    new_paths = (char **)realloc(list->paths, sizeof(char *) * (list->count + 1u));
    if (!new_paths) {
        return false;
    }
    list->paths = new_paths;

    new_names = (char **)realloc(list->names, sizeof(char *) * (list->count + 1u));
    if (!new_names) {
        return false;
    }
    list->names = new_names;

    new_kinds = (uint8_t *)realloc(list->kinds, sizeof(uint8_t) * (list->count + 1u));
    if (!new_kinds) {
        return false;
    }
    list->kinds = new_kinds;

    list->paths[list->count] = dup_text(path);
    list->names[list->count] = dup_text(name);
    list->kinds[list->count] = (uint8_t)kind;

    if (!list->paths[list->count] || !list->names[list->count]) {
        free(list->paths[list->count]);
        free(list->names[list->count]);
        list->paths[list->count] = NULL;
        list->names[list->count] = NULL;
        return false;
    }

    list->count++;
    return true;
}

static void sort_package_list(PackageList *list) {
    size_t i;
    size_t j;

    if (!list || list->count < 2u) {
        return;
    }

    for (i = 0; i + 1u < list->count; i++) {
        for (j = i + 1u; j < list->count; j++) {
            if (strcmp(list->names[i], list->names[j]) > 0) {
                char *tmp_path = list->paths[i];
                char *tmp_name = list->names[i];
                uint8_t tmp_kind = list->kinds[i];
                list->paths[i] = list->paths[j];
                list->names[i] = list->names[j];
                list->kinds[i] = list->kinds[j];
                list->paths[j] = tmp_path;
                list->names[j] = tmp_name;
                list->kinds[j] = tmp_kind;
            }
        }
    }
}

static void discover_packages(PackageList *list, char *status, size_t status_size) {
    DIR *dir;
    struct dirent *entry;

    free_package_list(list);

    dir = opendir(RAW_BIN_SCAN_DIR);
    if (dir) {
        while ((entry = readdir(dir)) != NULL) {
            char full_path[1024];
            char display_name[1024];
            struct stat st;

            if (entry->d_name[0] == '.') {
                continue;
            }
            if (!has_bin_extension(entry->d_name)) {
                continue;
            }

            snprintf(full_path, sizeof(full_path), "%s/%s", RAW_BIN_SCAN_DIR, entry->d_name);
            if (stat(full_path, &st) != 0 || !S_ISREG(st.st_mode)) {
                continue;
            }

            snprintf(display_name, sizeof(display_name), "[RAW] %s", entry->d_name);
            if (!append_package(list, full_path, display_name, PACKAGE_KIND_RAW_BIN)) {
                closedir(dir);
                if (status && status_size > 0u) {
                    snprintf(status, status_size, "Out of memory while scanning raw BIN list");
                }
                free_package_list(list);
                return;
            }
        }
        closedir(dir);
    }

    sort_package_list(list);

    if (status && status_size > 0u) {
        if (list->count == 0u) {
            snprintf(status, status_size, "No raw Rev6 .bin found in %s", RAW_BIN_SCAN_DIR);
        } else {
            snprintf(
                status,
                status_size,
                "Found %lu raw BIN item(s) in %s",
                (unsigned long)list->count,
                RAW_BIN_SCAN_DIR
            );
        }
    }
}

static const char *current_package_path(const PackageList *list) {
    if (list && list->count > 0u && list->index < list->count) {
        return list->paths[list->index];
    }
    return "";
}

static const char *current_package_name(const PackageList *list) {
    if (list && list->count > 0u && list->index < list->count) {
        return list->names[list->index];
    }
    return "No BIN selected";
}

static PackageKind current_package_kind(const PackageList *list) {
    if (list && list->count > 0u && list->index < list->count && list->kinds) {
        return (PackageKind)list->kinds[list->index];
    }
    return PACKAGE_KIND_RAW_BIN;
}

static RuntimeSpriteImage *build_runtime_sprite_images(
    const Av6Package *package,
    C2D_SpriteSheet *sheets,
    uint32_t sheet_count,
    char *err,
    size_t err_size
) {
    RuntimeSpriteImage *images;
    C2D_Image *base_images;
    uint32_t i;
    uint32_t mapped_count = 0u;

    if (!package || !sheets || sheet_count == 0u || package->sprite_count == 0u) {
        return NULL;
    }

    base_images = (C2D_Image *)calloc(sheet_count, sizeof(C2D_Image));
    if (!base_images) {
        if (err && err_size > 0u) {
            snprintf(err, err_size, "Out of memory while caching sprite-sheet images");
        }
        return NULL;
    }

    for (i = 0u; i < sheet_count; i++) {
        if (!sheets[i]) {
            continue;
        }
        base_images[i] = C2D_SpriteSheetGetImage(sheets[i], 0);
    }

    images = (RuntimeSpriteImage *)calloc(package->sprite_count, sizeof(RuntimeSpriteImage));
    if (!images) {
        free(base_images);
        if (err && err_size > 0u) {
            snprintf(err, err_size, "Out of memory allocating runtime sprite images");
        }
        return NULL;
    }

    for (i = 0u; i < package->sprite_count; i++) {
        const Av6Sprite *sprite = &package->sprites[i];
        RuntimeSpriteImage *item = &images[i];
        uint16_t sheet_index = sprite->sheet_index;
        C2D_Image base;
        float atlas_w;
        float atlas_h;
        float sprite_w;
        float sprite_h;
        float base_left;
        float base_top;
        float base_right;
        float base_bottom;
        float u0;
        float v0;
        float u1;
        float v1;

        if (sheet_index >= sheet_count) {
            continue;
        }

        base = base_images[sheet_index];
        if (!base.tex || !base.subtex) {
            continue;
        }

        atlas_w = safe_value((float)base.subtex->width, 0.0f);
        atlas_h = safe_value((float)base.subtex->height, 0.0f);
        if (atlas_w <= 0.0f || atlas_h <= 0.0f) {
            continue;
        }

        sprite_w = safe_value(sprite->w, 1.0f);
        sprite_h = safe_value(sprite->h, 1.0f);

        base_left = base.subtex->left;
        base_top = base.subtex->top;
        base_right = base.subtex->right;
        base_bottom = base.subtex->bottom;

        u0 = base_left + (sprite->x / atlas_w) * (base_right - base_left);
        v0 = base_top + (sprite->y / atlas_h) * (base_bottom - base_top);
        u1 = base_left + ((sprite->x + sprite_w) / atlas_w) * (base_right - base_left);
        v1 = base_top + ((sprite->y + sprite_h) / atlas_h) * (base_bottom - base_top);

        if (sprite->rotated) {
            /*
             * TexturePacker r="y" stores source pixels rotated in-atlas.
             * Keep top < bottom so Tex3DS marks this entry as rotated and
             * applies the expected quarter-turn UV decode path.
             */

            item->subtex.width = (u16)fmaxf(1.0f, sprite_h);
            item->subtex.height = (u16)fmaxf(1.0f, sprite_w);
            item->subtex.left = clamp01(v1);
            item->subtex.right = clamp01(v0);
            item->subtex.top = clamp01(u0);
            item->subtex.bottom = clamp01(u1);
        } else {
            item->subtex.width = (u16)fmaxf(1.0f, sprite_w);
            item->subtex.height = (u16)fmaxf(1.0f, sprite_h);
            item->subtex.left = clamp01(u0);
            item->subtex.top = clamp01(v0);
            item->subtex.right = clamp01(u1);
            item->subtex.bottom = clamp01(v1);
        }

        item->image.tex = base.tex;
        item->image.subtex = &item->subtex;
        mapped_count++;
    }

    free(base_images);

    if (mapped_count == 0u && err && err_size > 0u) {
        snprintf(err, err_size, "Spritesheet load succeeded, but no sprites mapped to valid texture sheets");
    }

    return images;
}
static int32_t find_layer_index_by_id(const Av6Package *package, int32_t id) {
    uint32_t i;

    if (!package) {
        return -1;
    }

    for (i = 0u; i < package->layer_count; i++) {
        if (package->layers[i].id == id) {
            return (int32_t)i;
        }
    }

    return -1;
}

static void runtime_unload(RuntimeState *runtime) {
    uint32_t i;

    if (!runtime) {
        return;
    }

    free(runtime->parent_index);
    runtime->parent_index = NULL;

    free(runtime->local_poses);
    runtime->local_poses = NULL;

    free(runtime->world_poses);
    runtime->world_poses = NULL;

    free(runtime->world_xforms);
    runtime->world_xforms = NULL;

    free(runtime->world_state);
    runtime->world_state = NULL;

    free(runtime->depth_target);
    runtime->depth_target = NULL;

    free(runtime->depth_smooth);
    runtime->depth_smooth = NULL;

    runtime->depth_profile_ready = false;

    free(runtime->runtime_images);
    runtime->runtime_images = NULL;

    if (runtime->sprite_sheets) {
        for (i = 0u; i < runtime->sprite_sheet_count; i++) {
            if (runtime->sprite_sheets[i]) {
                C2D_SpriteSheetFree(runtime->sprite_sheets[i]);
                runtime->sprite_sheets[i] = NULL;
            }
        }
        free(runtime->sprite_sheets);
        runtime->sprite_sheets = NULL;
    }
    runtime->sprite_sheet_count = 0u;

    if (runtime->loaded) {
        av6_free_package(&runtime->package);
        runtime->loaded = false;
    }

    runtime->textured_mode = false;
    runtime->load_error[0] = '\0';
    runtime->texture_status[0] = '\0';
    runtime->raw_anim_count = 0u;
    runtime->raw_anim_index = 0u;
    runtime->raw_anim_name[0] = '\0';
}

static bool runtime_prepare_pose_buffers(RuntimeState *runtime) {
    uint32_t i;

    runtime->parent_index = (int32_t *)malloc(sizeof(int32_t) * runtime->package.layer_count);
    runtime->local_poses = (Av6Pose *)calloc(runtime->package.layer_count, sizeof(Av6Pose));
    runtime->world_poses = (Av6Pose *)calloc(runtime->package.layer_count, sizeof(Av6Pose));
    runtime->world_xforms = (RuntimeWorldTransform *)calloc(runtime->package.layer_count, sizeof(RuntimeWorldTransform));
    runtime->world_state = (uint8_t *)calloc(runtime->package.layer_count, 1u);
    runtime->depth_target = (float *)calloc(runtime->package.layer_count, sizeof(float));
    runtime->depth_smooth = (float *)calloc(runtime->package.layer_count, sizeof(float));
    runtime->depth_profile_ready = false;

    if (!runtime->parent_index || !runtime->local_poses || !runtime->world_poses || !runtime->world_xforms || !runtime->world_state || !runtime->depth_target || !runtime->depth_smooth) {
        return false;
    }

    for (i = 0u; i < runtime->package.layer_count; i++) {
        int32_t parent_id = runtime->package.layers[i].parent_id;
        runtime->parent_index[i] = parent_id < 0 ? -1 : find_layer_index_by_id(&runtime->package, parent_id);
    }

    return true;
}

static void runtime_load(RuntimeState *runtime, const char *package_path, PackageKind kind, uint32_t raw_anim_index) {
    uint32_t loaded_sheet_count = 0u;

    runtime_unload(runtime);
    (void)kind;

    if (!package_path || !package_path[0]) {
        snprintf(runtime->load_error, sizeof(runtime->load_error), "No raw Rev6 .bin files found in %s", RAW_BIN_SCAN_DIR);
        runtime->loaded = false;
        runtime->textured_mode = false;
        return;
    }

    if (!raw_rev6_load_from_bin_with_animation(
            package_path,
            raw_anim_index,
            &runtime->package,
            &runtime->raw_anim_count,
            &runtime->raw_anim_index,
            runtime->raw_anim_name,
            sizeof(runtime->raw_anim_name),
            runtime->load_error,
            sizeof(runtime->load_error))) {
        runtime->loaded = false;
        runtime->textured_mode = false;
        return;
    }

    runtime->loaded = true;

    if (!runtime_prepare_pose_buffers(runtime)) {
        snprintf(runtime->load_error, sizeof(runtime->load_error), "Out of memory preparing layer hierarchy buffers");
        runtime_unload(runtime);
        return;
    }

    if (runtime->package.texture_blob_size > 0u && runtime->package.texture_blob) {
        runtime->sprite_sheets = (C2D_SpriteSheet *)calloc(1u, sizeof(C2D_SpriteSheet));
        if (!runtime->sprite_sheets) {
            snprintf(runtime->texture_status, sizeof(runtime->texture_status), "Out of memory allocating embedded spritesheet slot");
            runtime->textured_mode = false;
            return;
        }

        runtime->sprite_sheets[0] = C2D_SpriteSheetLoadFromMem(runtime->package.texture_blob, runtime->package.texture_blob_size);
        if (!runtime->sprite_sheets[0]) {
            snprintf(runtime->texture_status, sizeof(runtime->texture_status), "Texture blob present but could not be loaded as SpriteSheet");
            runtime->textured_mode = false;
            return;
        }

        runtime->sprite_sheet_count = 1u;
        loaded_sheet_count = 1u;
        runtime->runtime_images = build_runtime_sprite_images(
            &runtime->package,
            runtime->sprite_sheets,
            runtime->sprite_sheet_count,
            runtime->texture_status,
            sizeof(runtime->texture_status)
        );

        if (!runtime->runtime_images) {
            if (runtime->texture_status[0] == '\0') {
                snprintf(runtime->texture_status, sizeof(runtime->texture_status), "Sprite image mapping failed");
            }
            runtime->textured_mode = false;
            return;
        }

        runtime->textured_mode = true;
        snprintf(
            runtime->texture_status,
            sizeof(runtime->texture_status),
            "Textured mode enabled (embedded %lu bytes)",
            (unsigned long)runtime->package.texture_blob_size
        );
        return;
    }

    if (runtime->package.external_sheet_count > 0u && runtime->package.external_sheet_paths) {
        uint32_t i;
        runtime->sprite_sheets = (C2D_SpriteSheet *)calloc(runtime->package.external_sheet_count, sizeof(C2D_SpriteSheet));
        if (!runtime->sprite_sheets) {
            snprintf(runtime->texture_status, sizeof(runtime->texture_status), "Out of memory allocating external spritesheet slots");
            runtime->textured_mode = false;
            return;
        }

        runtime->sprite_sheet_count = runtime->package.external_sheet_count;

        for (i = 0u; i < runtime->sprite_sheet_count; i++) {
            const char *sheet_path = runtime->package.external_sheet_paths[i];
            if (!sheet_path || !sheet_path[0]) {
                continue;
            }
            runtime->sprite_sheets[i] = C2D_SpriteSheetLoad(sheet_path);
            if (runtime->sprite_sheets[i]) {
                loaded_sheet_count++;
            }
        }

        if (loaded_sheet_count > 0u) {
            runtime->runtime_images = build_runtime_sprite_images(
                &runtime->package,
                runtime->sprite_sheets,
                runtime->sprite_sheet_count,
                runtime->texture_status,
                sizeof(runtime->texture_status)
            );
        }

        if (runtime->runtime_images && loaded_sheet_count > 0u) {
            runtime->textured_mode = true;
            snprintf(
                runtime->texture_status,
                sizeof(runtime->texture_status),
                "Textured mode enabled (%lu/%lu external .t3x loaded)",
                (unsigned long)loaded_sheet_count,
                (unsigned long)runtime->sprite_sheet_count
            );
        } else {
            runtime->textured_mode = false;
            if (runtime->texture_status[0] == '\0') {
                snprintf(
                    runtime->texture_status,
                    sizeof(runtime->texture_status),
                    "No external .t3x sheets could be loaded (debug quads mode)"
                );
            }
        }
        return;
    }

    runtime->textured_mode = false;
    snprintf(runtime->texture_status, sizeof(runtime->texture_status), "No embedded or external spritesheets found (debug quads mode)");
}
static Av6Pose resolve_world_pose(RuntimeState *runtime, uint32_t layer_index);

static uint32_t runtime_layer_chain_depth(const RuntimeState *runtime, uint32_t layer_index) {
    uint32_t depth = 0u;
    uint32_t guard = 0u;
    int32_t parent;

    if (!runtime || !runtime->parent_index || layer_index >= runtime->package.layer_count) {
        return 0u;
    }

    parent = runtime->parent_index[layer_index];
    while (parent >= 0 && (uint32_t)parent < runtime->package.layer_count && guard < runtime->package.layer_count) {
        depth++;
        parent = runtime->parent_index[parent];
        guard++;
    }

    return depth;
}

static void runtime_prepare_frame_poses(RuntimeState *runtime, float time_sec) {
    uint32_t i;

    if (!runtime || !runtime->loaded || !runtime->local_poses || !runtime->world_poses || !runtime->world_xforms || !runtime->world_state) {
        return;
    }

    memset(runtime->world_state, 0, runtime->package.layer_count);
    memset(runtime->world_xforms, 0, sizeof(RuntimeWorldTransform) * runtime->package.layer_count);

    for (i = 0u; i < runtime->package.layer_count; i++) {
        runtime->local_poses[i] = av6_eval_layer_pose(&runtime->package, i, time_sec);
    }

    for (i = 0u; i < runtime->package.layer_count; i++) {
        resolve_world_pose(runtime, i);
    }
}

static void runtime_compute_rig_depth(RuntimeState *runtime) {
    uint32_t i;
    uint32_t n;
    uint32_t max_chain_depth = 1u;
    float y_min = 1.0e30f;
    float y_max = -1.0e30f;
    float scale_min = 1.0e30f;
    float scale_max = -1.0e30f;
    float y_range;
    float scale_range;

    if (!runtime || !runtime->depth_target || !runtime->depth_smooth || !runtime->world_poses || !runtime->package.layers) {
        return;
    }

    n = runtime->package.layer_count;
    if (n == 0u) {
        return;
    }

    for (i = 0u; i < n; i++) {
        const Av6Pose *pose = &runtime->world_poses[i];
        float y_value = pose->valid ? pose->y : 0.0f;
        float scale_value = 1.0f;
        uint32_t chain_depth = runtime_layer_chain_depth(runtime, i);

        if (runtime->world_xforms && runtime->world_xforms[i].valid) {
            const RuntimeWorldTransform *xf = &runtime->world_xforms[i];
            float sx = sqrtf(xf->m00 * xf->m00 + xf->m10 * xf->m10);
            float sy = sqrtf(xf->m01 * xf->m01 + xf->m11 * xf->m11);
            y_value = xf->anchor_world_y;
            scale_value = 0.5f * (fabsf(sx) + fabsf(sy));
        } else if (pose->valid) {
            scale_value = 0.5f * (fabsf(pose->scale_x) + fabsf(pose->scale_y)) / 100.0f;
        }

        if (scale_value < 0.0001f || !isfinite(scale_value)) {
            scale_value = 1.0f;
        }

        if (y_value < y_min) {
            y_min = y_value;
        }
        if (y_value > y_max) {
            y_max = y_value;
        }
        if (scale_value < scale_min) {
            scale_min = scale_value;
        }
        if (scale_value > scale_max) {
            scale_max = scale_value;
        }

        if (chain_depth > max_chain_depth) {
            max_chain_depth = chain_depth;
        }
    }

    y_range = y_max - y_min;
    if (y_range < 0.0001f || !isfinite(y_range)) {
        y_range = 1.0f;
    }

    scale_range = scale_max - scale_min;
    if (scale_range < 0.0001f || !isfinite(scale_range)) {
        scale_range = 1.0f;
    }

    for (i = 0u; i < n; i++) {
        const Av6Pose *pose = &runtime->world_poses[i];
        float order_norm = (n > 1u) ? ((float)i / (float)(n - 1u)) : 0.5f;
        float z = 0.5f - order_norm;
        float y_value = pose->valid ? pose->y : 0.0f;
        float scale_value = 1.0f;
        float y_term;
        float scale_term;
        float hierarchy_term;
        float chain_norm;
        uint32_t chain_depth = runtime_layer_chain_depth(runtime, i);

        if (runtime->world_xforms && runtime->world_xforms[i].valid) {
            const RuntimeWorldTransform *xf = &runtime->world_xforms[i];
            float sx = sqrtf(xf->m00 * xf->m00 + xf->m10 * xf->m10);
            float sy = sqrtf(xf->m01 * xf->m01 + xf->m11 * xf->m11);
            y_value = xf->anchor_world_y;
            scale_value = 0.5f * (fabsf(sx) + fabsf(sy));
        } else if (pose->valid) {
            scale_value = 0.5f * (fabsf(pose->scale_x) + fabsf(pose->scale_y)) / 100.0f;
        }

        if (scale_value < 0.0001f || !isfinite(scale_value)) {
            scale_value = 1.0f;
        }

        y_term = ((y_value - y_min) / y_range) - 0.5f;
        scale_term = ((scale_value - scale_min) / scale_range) - 0.5f;
        chain_norm = (max_chain_depth > 0u) ? ((float)chain_depth / (float)max_chain_depth) : 0.0f;
        hierarchy_term = chain_norm - 0.5f;

        z += 0.20f * y_term;
        z += 0.10f * scale_term;
        z += 0.05f * hierarchy_term;

        runtime->depth_target[i] = clampf_range(z, -0.62f, 0.62f);
    }

    for (i = 0u; i < 3u; i++) {
        uint32_t j;

        for (j = 0u; j < n; j++) {
            int32_t parent = runtime->parent_index ? runtime->parent_index[j] : -1;
            float z = runtime->depth_target[j];

            if (parent >= 0 && (uint32_t)parent < n) {
                float parent_z = runtime->depth_target[parent];
                float band = runtime->package.layers[j].blend == 0u ? 0.16f : 0.08f;

                if (z > parent_z + band) {
                    z = parent_z + band;
                }
                if (z < parent_z - band) {
                    z = parent_z - band;
                }

                if (runtime->package.layers[j].blend == 0u) {
                    z = z * 0.65f + parent_z * 0.35f;
                } else {
                    z = z * 0.40f + parent_z * 0.60f;
                }
            }

            runtime->depth_target[j] = clampf_range(z, -0.62f, 0.62f);
        }

        if (n > 1u) {
            float prev = runtime->depth_target[0u];
            for (j = 1u; j < n; j++) {
                float max_allowed = prev - 0.003f;
                if (runtime->depth_target[j] > max_allowed) {
                    runtime->depth_target[j] = max_allowed;
                }
                prev = runtime->depth_target[j];
            }
        }
    }

    if (!runtime->depth_profile_ready) {
        memcpy(runtime->depth_smooth, runtime->depth_target, sizeof(float) * n);
        runtime->depth_profile_ready = true;
    } else {
        for (i = 0u; i < n; i++) {
            runtime->depth_smooth[i] = runtime->depth_smooth[i] * 0.78f + runtime->depth_target[i] * 0.22f;
        }
    }

    if (n > 1u) {
        float prev = runtime->depth_smooth[0u];
        for (i = 1u; i < n; i++) {
            float max_allowed = prev - 0.003f;
            if (runtime->depth_smooth[i] > max_allowed) {
                runtime->depth_smooth[i] = max_allowed;
            }
            runtime->depth_smooth[i] = clampf_range(runtime->depth_smooth[i], -0.62f, 0.62f);
            prev = runtime->depth_smooth[i];
        }
        runtime->depth_smooth[0u] = clampf_range(runtime->depth_smooth[0u], -0.62f, 0.62f);
    } else {
        runtime->depth_smooth[0u] = clampf_range(runtime->depth_smooth[0u], -0.62f, 0.62f);
    }
}

static Av6Pose resolve_world_pose(RuntimeState *runtime, uint32_t layer_index) {
    Av6Pose local;
    Av6Pose result;
    Av6Pose empty;
    RuntimeWorldTransform local_xf;
    RuntimeWorldTransform result_xf;

    memset(&empty, 0, sizeof(empty));
    memset(&local_xf, 0, sizeof(local_xf));
    memset(&result_xf, 0, sizeof(result_xf));

    if (!runtime || layer_index >= runtime->package.layer_count) {
        return empty;
    }

    if (runtime->world_state[layer_index] == 2u) {
        return runtime->world_poses[layer_index];
    }

    if (runtime->world_state[layer_index] == 1u) {
        result = runtime->local_poses[layer_index];
        runtime->world_poses[layer_index] = result;
        if (runtime->world_xforms) {
            build_local_transform(&runtime->package.layers[layer_index], &result, &runtime->world_xforms[layer_index]);
        }
        runtime->world_state[layer_index] = 2u;
        return result;
    }

    runtime->world_state[layer_index] = 1u;

    local = runtime->local_poses[layer_index];
    result = local;

    if (local.valid) {
        const Av6Layer *layer = &runtime->package.layers[layer_index];
        int32_t parent = runtime->parent_index[layer_index];

        build_local_transform(layer, &local, &local_xf);
        result_xf = local_xf;

        if (parent >= 0 && (uint32_t)parent < runtime->package.layer_count) {
            Av6Pose parent_pose = resolve_world_pose(runtime, (uint32_t)parent);
            if (parent_pose.valid && runtime->world_xforms && runtime->world_xforms[parent].valid && local_xf.valid) {
                const RuntimeWorldTransform *p = &runtime->world_xforms[parent];
                RuntimeWorldTransform world_xf;
                float world_sx;
                float world_sy;
                float det;

                memset(&world_xf, 0, sizeof(world_xf));
                world_xf.valid = 1u;

                world_xf.m00 = p->m00 * local_xf.m00 + p->m01 * local_xf.m10;
                world_xf.m01 = p->m00 * local_xf.m01 + p->m01 * local_xf.m11;
                world_xf.m10 = p->m10 * local_xf.m00 + p->m11 * local_xf.m10;
                world_xf.m11 = p->m10 * local_xf.m01 + p->m11 * local_xf.m11;

                world_xf.tx = p->m00 * local_xf.tx + p->m01 * local_xf.ty + p->tx;
                world_xf.ty = p->m10 * local_xf.tx + p->m11 * local_xf.ty + p->ty;

                world_xf.anchor_world_x = p->m00 * local.x + p->m01 * local.y + p->tx;
                world_xf.anchor_world_y = p->m10 * local.x + p->m11 * local.y + p->ty;

                result_xf = world_xf;
                result.x = world_xf.anchor_world_x;
                result.y = world_xf.anchor_world_y;

                world_sx = sqrtf(world_xf.m00 * world_xf.m00 + world_xf.m10 * world_xf.m10);
                world_sy = sqrtf(world_xf.m01 * world_xf.m01 + world_xf.m11 * world_xf.m11);
                det = world_xf.m00 * world_xf.m11 - world_xf.m01 * world_xf.m10;
                if (det < 0.0f) {
                    world_sy = -world_sy;
                }

                result.scale_x = world_sx * 100.0f;
                result.scale_y = world_sy * 100.0f;
                result.rotation = atan2f(world_xf.m10, world_xf.m00) * (180.0f / (float)M_PI);

                result.opacity = local.opacity;
                result.r = local.r;
                result.g = local.g;
                result.b = local.b;
                result.a = local.a;
            }
        }
    }

    runtime->world_poses[layer_index] = result;
    if (runtime->world_xforms) {
        runtime->world_xforms[layer_index] = result_xf;
    }
    runtime->world_state[layer_index] = 2u;
    return result;
}
int main(int argc, char **argv) {
    PackageList packages;
    RuntimeState runtime;
    char package_discovery[256];

    bool paused = false;
    bool depth_mode = false;
    float speed = 1.0f;
    float zoom = 1.0f;
    float depth_strength = 0.0f;
    float depth_tilt_x = 0.0f;
    float depth_tilt_y = 0.0f;
    const float depth_parallax_px = 48.0f;
    const float stereo_eye_px = 28.0f;
    const float depth_perspective = 0.28f;
    float time_sec = 0.0f;
    u64 last_ms;

    C3D_RenderTarget *top_left;
    C3D_RenderTarget *top_right;
    C3D_RenderTarget *bottom;
    C2D_TextBuf text_buf;
    C2D_Text text;

    (void)argc;
    (void)argv;

    memset(&packages, 0, sizeof(packages));
    memset(&runtime, 0, sizeof(runtime));
    package_discovery[0] = '\0';

    gfxInitDefault();
    romfsInit();
    C3D_Init(C3D_DEFAULT_CMDBUF_SIZE);
    C2D_Init(C2D_DEFAULT_MAX_OBJECTS);
    C2D_Prepare();
    C2D_SetTintMode(C2D_TintMult);

    top_left = C2D_CreateScreenTarget(GFX_TOP, GFX_LEFT);
    top_right = C2D_CreateScreenTarget(GFX_TOP, GFX_RIGHT);
    bottom = C2D_CreateScreenTarget(GFX_BOTTOM, GFX_LEFT);
    text_buf = C2D_TextBufNew(2048);

    discover_packages(&packages, package_discovery, sizeof(package_discovery));
    runtime_load(&runtime, current_package_path(&packages), current_package_kind(&packages), 0u);

    last_ms = osGetTime();

    while (aptMainLoop()) {
        hidScanInput();

        {
            const u32 k_down = hidKeysDown();

            if (k_down & KEY_START) {
                break;
            }
            if (k_down & KEY_A) {
                paused = !paused;
            }
            if (k_down & KEY_X) {
                time_sec = 0.0f;
            }
            if (k_down & KEY_DUP) {
                speed += 0.1f;
                if (speed > 4.0f) {
                    speed = 4.0f;
                }
            }
            if (k_down & KEY_DDOWN) {
                speed -= 0.1f;
                if (speed < 0.1f) {
                    speed = 0.1f;
                }
            }
            if (k_down & KEY_L) {
                zoom -= 0.1f;
                if (zoom < 0.25f) {
                    zoom = 0.25f;
                }
            }
            if (k_down & KEY_R) {
                zoom += 0.1f;
                if (zoom > 4.0f) {
                    zoom = 4.0f;
                }
            }

            if (packages.count > 1u && (k_down & KEY_DLEFT)) {
                if (packages.index == 0u) {
                    packages.index = packages.count - 1u;
                } else {
                    packages.index--;
                }
                time_sec = 0.0f;
                runtime_load(&runtime, current_package_path(&packages), current_package_kind(&packages), 0u);
            }

            if (packages.count > 1u && (k_down & KEY_DRIGHT)) {
                packages.index = (packages.index + 1u) % packages.count;
                time_sec = 0.0f;
                runtime_load(&runtime, current_package_path(&packages), current_package_kind(&packages), 0u);
            }

            if (k_down & KEY_SELECT) {
                PackageKind active_kind = current_package_kind(&packages);
                if (active_kind == PACKAGE_KIND_RAW_BIN && runtime.raw_anim_count > 1u) {
                    uint32_t next_anim = (runtime.raw_anim_index + 1u) % runtime.raw_anim_count;
                    time_sec = 0.0f;
                    runtime_load(&runtime, current_package_path(&packages), active_kind, next_anim);
                } else if (packages.count > 1u) {
                    packages.index = (packages.index + 1u) % packages.count;
                    time_sec = 0.0f;
                    runtime_load(&runtime, current_package_path(&packages), current_package_kind(&packages), 0u);
                }
            }

            if (k_down & KEY_Y) {
                time_sec = 0.0f;
                runtime_load(&runtime, current_package_path(&packages), current_package_kind(&packages), runtime.raw_anim_index);
            }
        }

        {
            circlePosition circle;
            float target_tilt_x;
            float target_tilt_y;
            float slider = osGet3DSliderState();

            if (slider < 0.0f) {
                slider = 0.0f;
            } else if (slider > 1.0f) {
                slider = 1.0f;
            }
            depth_strength = slider;
            depth_mode = (depth_strength > 0.01f);

            hidCircleRead(&circle);
            target_tilt_x = depth_mode ? (((float)circle.dx / 156.0f) * depth_strength) : 0.0f;
            target_tilt_y = depth_mode ? ((-(float)circle.dy / 156.0f) * depth_strength) : 0.0f;

            if (target_tilt_x < -1.0f) {
                target_tilt_x = -1.0f;
            } else if (target_tilt_x > 1.0f) {
                target_tilt_x = 1.0f;
            }
            if (target_tilt_y < -1.0f) {
                target_tilt_y = -1.0f;
            } else if (target_tilt_y > 1.0f) {
                target_tilt_y = 1.0f;
            }

            depth_tilt_x = depth_tilt_x * 0.82f + target_tilt_x * 0.18f;
            depth_tilt_y = depth_tilt_y * 0.82f + target_tilt_y * 0.18f;
        }

        {
            u64 now_ms = osGetTime();
            float dt = (float)(now_ms - last_ms) / 1000.0f;
            last_ms = now_ms;

            if (runtime.loaded && !paused) {
                time_sec += dt * speed;
                if (runtime.package.duration > 0.0001f) {
                    while (time_sec > runtime.package.duration) {
                        time_sec -= runtime.package.duration;
                    }
                }
            }
        }

        if (runtime.loaded) {
            runtime_prepare_frame_poses(&runtime, time_sec);
            runtime_compute_rig_depth(&runtime);
        }

        gfxSet3D(depth_mode);

        C3D_FrameBegin(C3D_FRAME_SYNCDRAW);
        C2D_TargetClear(top_left, C2D_Color32(16, 18, 26, 255));
        if (depth_mode) {
            C2D_TargetClear(top_right, C2D_Color32(16, 18, 26, 255));
        }
        C2D_TargetClear(bottom, C2D_Color32(12, 14, 20, 255));

        {
            int eye_count = depth_mode ? 2 : 1;
            int eye_index;

            for (eye_index = 0; eye_index < eye_count; eye_index++) {
                float eye_sign = (eye_index == 0) ? 1.0f : -1.0f;
                C2D_SceneBegin(eye_index == 0 ? top_left : top_right);

                if (runtime.loaded) {
            uint32_t layer_index;
            const float fit_scale_x = TOP_WIDTH / fmaxf(1.0f, runtime.package.anim_width);
            const float fit_scale_y = TOP_HEIGHT / fmaxf(1.0f, runtime.package.anim_height);
            const float origin_x = -0.5f * runtime.package.anim_width;
            const float origin_y = runtime.package.centered
                ? (-0.25f * runtime.package.anim_height)
                : (-0.5f * runtime.package.anim_height);
            float fit_scale = fminf(fit_scale_x, fit_scale_y);
            float fit_offset_x;
            float fit_offset_y;
            C3D_Mtx base_view;

            if (!(fit_scale > 0.0001f) || !isfinite(fit_scale)) {
                fit_scale = 1.0f;
            }
            fit_scale *= zoom;
            fit_offset_x = TOP_WIDTH * 0.5f;
            fit_offset_y = TOP_HEIGHT * 0.5f;

            C2D_ViewSave(&base_view);

            for (layer_index = runtime.package.layer_count; layer_index > 0u; layer_index--) {
                uint32_t draw_layer_index = layer_index - 1u;
                Av6Pose pose;
                const RuntimeWorldTransform *world_xf = NULL;

                if (runtime.world_poses && runtime.world_xforms) {
                    pose = runtime.world_poses[draw_layer_index];
                    if (runtime.world_xforms[draw_layer_index].valid) {
                        world_xf = &runtime.world_xforms[draw_layer_index];
                    }
                } else {
                    pose = av6_eval_layer_pose(&runtime.package, draw_layer_index, time_sec);
                }

                if (!pose.valid) {
                    continue;
                }

                {
                    float depth_z = 0.0f;
                    if (runtime.depth_smooth && draw_layer_index < runtime.package.layer_count) {
                        depth_z = runtime.depth_smooth[draw_layer_index];
                    } else {
                        float depth_norm = (runtime.package.layer_count > 1u)
                            ? ((float)draw_layer_index / (float)(runtime.package.layer_count - 1u))
                            : 0.5f;
                        depth_z = 0.5f - depth_norm;
                    }
                    float depth_offset_x = 0.0f;
                    float depth_offset_y = 0.0f;
                    float depth_scale = 1.0f;
                    float effective_alpha = ((float)pose.a) * (pose.opacity / 100.0f);

                    if (depth_mode) {
                        float stereo_offset_x = eye_sign * depth_z * stereo_eye_px * depth_strength;
                        depth_offset_x = depth_tilt_x * depth_z * depth_parallax_px + stereo_offset_x;
                        depth_offset_y = depth_tilt_y * depth_z * depth_parallax_px;
                        depth_scale = 1.0f + depth_perspective * depth_z;
                        if (depth_scale < 0.2f) {
                            depth_scale = 0.2f;
                        }
                        if (!isfinite(depth_scale)) {
                            depth_scale = 1.0f;
                        }
                    }
                    u32 color;
                    bool drew_sprite = false;

                    if (effective_alpha < 0.0f) {
                        effective_alpha = 0.0f;
                    }
                    if (effective_alpha > 255.0f) {
                        effective_alpha = 255.0f;
                    }

                    color = C2D_Color32(
                        pose.r,
                        pose.g,
                        pose.b,
                        (u8)(effective_alpha + 0.5f)
                    );

                    if (
                        runtime.textured_mode
                        && runtime.runtime_images
                        && pose.sprite_index != AV6_NO_SPRITE
                        && pose.sprite_index < runtime.package.sprite_count
                    ) {
                        const Av6Sprite *sprite = &runtime.package.sprites[pose.sprite_index];
                        const Av6Layer *layer = &runtime.package.layers[draw_layer_index];
                        const RuntimeSpriteImage *runtime_image = &runtime.runtime_images[pose.sprite_index];
                        float draw_scale = sprite->draw_scale;
                        float source_w = safe_value(sprite->w, 32.0f);
                        float source_h = safe_value(sprite->h, 32.0f);
                        float frame_x;
                        float frame_y;
                        C2D_ImageTint tint;
                        C2D_DrawParams draw_params;

                        if (!(draw_scale > 0.0001f) || !isfinite(draw_scale)) {
                            draw_scale = 1.0f;
                        }

                        source_w *= draw_scale;
                        source_h *= draw_scale;
                        frame_x = sprite->frame_x * draw_scale;
                        frame_y = sprite->frame_y * draw_scale;

                        if (sprite->rotated) {
                            float tmp = source_w;
                            source_w = source_h;
                            source_h = tmp;
                        }

                        C2D_PlainImageTint(&tint, color, 1.0f);

                        if (runtime_image->image.tex && runtime_image->image.subtex) {
                            if (world_xf && world_xf->valid) {
                                apply_world_view_transform(
                                    world_xf,
                                    origin_x,
                                    origin_y,
                                    fit_scale,
                                    fit_offset_x,
                                    fit_offset_y,
                                    depth_offset_x,
                                    depth_offset_y,
                                    depth_scale
                                );

                                draw_params.pos.x = frame_x;
                                draw_params.pos.y = frame_y;
                                draw_params.pos.w = source_w;
                                draw_params.pos.h = source_h;
                                draw_params.center.x = 0.0f;
                                draw_params.center.y = 0.0f;
                                draw_params.depth = 0.5f;
                                draw_params.angle = 0.0f;

                                if (sprite->rotated) {
                                    draw_params.center.x = 0.5f * draw_params.pos.w;
                                    draw_params.center.y = 0.5f * draw_params.pos.h;
                                    draw_params.pos.x = frame_x + draw_params.center.x;
                                    draw_params.pos.y = frame_y + draw_params.center.y;
                                    draw_params.angle = (float)M_PI;
                                }
                            } else {
                                float scale_x = pose.scale_x / 100.0f;
                                float scale_y = pose.scale_y / 100.0f;
                                float anchor_x_px;
                                float anchor_y_px;
                                float frame_left;
                                float frame_top;
                                float sprite_left;
                                float sprite_top;
                                float sprite_draw_w;
                                float sprite_draw_h;
                                float rotation_radians;
                                float pivot_x;
                                float pivot_y;

                                if (fabsf(scale_x) < 0.0001f) {
                                    scale_x = scale_x < 0.0f ? -0.0001f : 0.0001f;
                                }
                                if (fabsf(scale_y) < 0.0001f) {
                                    scale_y = scale_y < 0.0f ? -0.0001f : 0.0001f;
                                }

                                anchor_x_px = layer->anchor_x * scale_x;
                                anchor_y_px = layer->anchor_y * scale_y;
                                frame_left = origin_x + pose.x - anchor_x_px;
                                frame_top = origin_y + pose.y - anchor_y_px;

                                sprite_left = frame_left + (frame_x * scale_x);
                                sprite_top = frame_top + (frame_y * scale_y);
                                sprite_draw_w = source_w * scale_x;
                                sprite_draw_h = source_h * scale_y;
                                rotation_radians = pose.rotation * ((float)M_PI / 180.0f);
                                pivot_x = anchor_x_px - (frame_x * scale_x);
                                pivot_y = anchor_y_px - (frame_y * scale_y);

                                if (fabsf(sprite_draw_w) < 1.0f) {
                                    sprite_draw_w = sprite_draw_w < 0.0f ? -1.0f : 1.0f;
                                }
                                if (fabsf(sprite_draw_h) < 1.0f) {
                                    sprite_draw_h = sprite_draw_h < 0.0f ? -1.0f : 1.0f;
                                }

                                draw_params.pos.x = fit_offset_x + fit_scale * sprite_left;
                                draw_params.pos.y = fit_offset_y + fit_scale * sprite_top;
                                draw_params.pos.w = fit_scale * sprite_draw_w;
                                draw_params.pos.h = fit_scale * sprite_draw_h;
                                draw_params.center.x = fit_scale * pivot_x;
                                draw_params.center.y = fit_scale * pivot_y;
                                draw_params.pos.x += depth_offset_x;
                                draw_params.pos.y += depth_offset_y;
                                draw_params.pos.w *= depth_scale;
                                draw_params.pos.h *= depth_scale;
                                draw_params.center.x *= depth_scale;
                                draw_params.center.y *= depth_scale;
                                draw_params.depth = 0.5f;
                                draw_params.angle = rotation_radians;
                            }

                            C2D_DrawImage(runtime_image->image, &draw_params, &tint);
                            if (world_xf && world_xf->valid) {
                                C2D_ViewRestore(&base_view);
                            }
                            drew_sprite = true;
                        }
                    }
                    if (!drew_sprite) {
                        float draw_w = fmaxf(1.0f, 32.0f * (pose.scale_x / 100.0f));
                        float draw_h = fmaxf(1.0f, 32.0f * (pose.scale_y / 100.0f));
                        if (depth_mode) {
                            draw_w *= depth_scale;
                            draw_h *= depth_scale;
                        }
                        float draw_x = origin_x + pose.x - (draw_w * 0.5f);
                        float draw_y = origin_y + pose.y - (draw_h * 0.5f);
                        C2D_DrawRectSolid(
                            fit_offset_x + fit_scale * draw_x + depth_offset_x,
                            fit_offset_y + fit_scale * draw_y + depth_offset_y,
                            0.5f,
                            fit_scale * draw_w,
                            fit_scale * draw_h,
                            color
                        );
                    }
                }
            }

            C2D_ViewRestore(&base_view);
                }
            }
        }

        C2D_SceneBegin(bottom);
        if (runtime.loaded) {
            char status[320];
            char package_line[320];
            char controls_line_1[320];
            char controls_line_2[320];

            snprintf(
                status,
                sizeof(status),
                "v%lu C:%u L:%lu S:%lu K:%lu t=%.2f/%.2f speed=%.1fx zoom=%.2fx 3D=%.2f",
                (unsigned long)runtime.package.version,
                (unsigned int)runtime.package.centered,
                (unsigned long)runtime.package.layer_count,
                (unsigned long)runtime.package.sprite_count,
                (unsigned long)runtime.package.keyframe_count,
                time_sec,
                runtime.package.duration,
                speed,
                zoom,
                depth_strength
            );

            {
                PackageKind active_kind = current_package_kind(&packages);
                bool has_anim_cycle = (active_kind == PACKAGE_KIND_RAW_BIN && runtime.raw_anim_count > 1u);

                if (packages.count > 0u) {
                    if (active_kind == PACKAGE_KIND_RAW_BIN && runtime.raw_anim_count > 0u) {
                        if (runtime.raw_anim_name[0]) {
                            snprintf(
                                package_line,
                                sizeof(package_line),
                                "BIN [%lu/%lu]: %s  Anim [%lu/%lu]: %s",
                                (unsigned long)(packages.index + 1u),
                                (unsigned long)packages.count,
                                current_package_name(&packages),
                                (unsigned long)(runtime.raw_anim_index + 1u),
                                (unsigned long)runtime.raw_anim_count,
                                runtime.raw_anim_name
                            );
                        } else {
                            snprintf(
                                package_line,
                                sizeof(package_line),
                                "BIN [%lu/%lu]: %s  Anim [%lu/%lu]",
                                (unsigned long)(packages.index + 1u),
                                (unsigned long)packages.count,
                                current_package_name(&packages),
                                (unsigned long)(runtime.raw_anim_index + 1u),
                                (unsigned long)runtime.raw_anim_count
                            );
                        }
                    } else {
                        snprintf(
                            package_line,
                            sizeof(package_line),
                            "BIN [%lu/%lu]: %s",
                            (unsigned long)(packages.index + 1u),
                            (unsigned long)packages.count,
                            current_package_name(&packages)
                        );
                    }
                } else {
                    snprintf(package_line, sizeof(package_line), "BIN: %s", current_package_name(NULL));
                }

                if (packages.count > 1u) {
                    if (has_anim_cycle) {
                        snprintf(controls_line_1, sizeof(controls_line_1), "A Pause X Restart Y Reload SELECT Next Anim START Exit");
                    } else {
                        snprintf(controls_line_1, sizeof(controls_line_1), "A Pause X Restart Y Reload SELECT Next BIN START Exit");
                    }
                    snprintf(controls_line_2, sizeof(controls_line_2), "L/R Zoom  Up/Down Speed  3D Slider Depth  Circle Tilt  DPad L/R Switch");
                } else {
                    if (has_anim_cycle) {
                        snprintf(controls_line_1, sizeof(controls_line_1), "A Pause X Restart Y Reload SELECT Next Anim START Exit");
                    } else {
                        snprintf(controls_line_1, sizeof(controls_line_1), "A Pause X Restart Y Reload START Exit");
                    }
                    snprintf(controls_line_2, sizeof(controls_line_2), "L/R Zoom  Up/Down Speed  3D Slider Depth  Circle Tilt");
                }
            }

            draw_text_line(text_buf, &text, 6.0f, 16.0f, status, C2D_Color32(220, 235, 255, 255));
            draw_text_line(
                text_buf,
                &text,
                6.0f,
                32.0f,
                runtime.texture_status[0] ? runtime.texture_status : "Texture status unavailable",
                runtime.textured_mode ? C2D_Color32(120, 230, 150, 255) : C2D_Color32(255, 190, 140, 255)
            );
            draw_text_line(text_buf, &text, 6.0f, 48.0f, package_line, C2D_Color32(175, 210, 255, 255));
            draw_text_line(text_buf, &text, 6.0f, 64.0f, controls_line_1, C2D_Color32(170, 190, 220, 255));
            draw_text_line(text_buf, &text, 6.0f, 80.0f, controls_line_2, C2D_Color32(170, 190, 220, 255));
            {
                char depth_line[160];
                snprintf(
                    depth_line,
                    sizeof(depth_line),
                    "2.5D %s  slider=%.2f  tiltX=%+.2f tiltY=%+.2f",
                    depth_mode ? "ON" : "OFF",
                    depth_strength,
                    depth_tilt_x,
                    depth_tilt_y
                );
                draw_text_line(text_buf, &text, 6.0f, 96.0f, depth_line, C2D_Color32(180, 220, 255, 255));
            }
            draw_text_line(
                text_buf,
                &text,
                6.0f,
                112.0f,
                package_discovery[0] ? package_discovery : "",
                C2D_Color32(200, 200, 200, 255)
            );
        } else {
            char error_line[320];
            char package_line[320];

            snprintf(error_line, sizeof(error_line), "Failed to load BIN: %s", current_package_path(&packages)[0] ? current_package_path(&packages) : "(none)");
            snprintf(package_line, sizeof(package_line), "BIN source: %s", current_package_name(&packages));

            draw_text_line(text_buf, &text, 6.0f, 20.0f, error_line, C2D_Color32(255, 180, 180, 255));
            draw_text_line(text_buf, &text, 6.0f, 36.0f, runtime.load_error, C2D_Color32(255, 160, 160, 255));
            draw_text_line(text_buf, &text, 6.0f, 52.0f, package_line, C2D_Color32(180, 210, 255, 255));
            draw_text_line(
                text_buf,
                &text,
                6.0f,
                68.0f,
                package_discovery[0] ? package_discovery : "Copy raw Rev6 .bin files to sdmc:/3ds/aniviewer3ds/raw/",
                C2D_Color32(220, 220, 220, 255)
            );
        }

        C3D_FrameEnd(0);
    }

    runtime_unload(&runtime);
    free_package_list(&packages);

    gfxSet3D(false);

    C2D_TextBufDelete(text_buf);
    C2D_Fini();
    C3D_Fini();
    romfsExit();
    gfxExit();

    return 0;
}
