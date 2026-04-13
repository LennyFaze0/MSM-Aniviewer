#---------------------------------------------------------------------------------
# AniViewer 3DS Alpha - simple non-recursive build
#---------------------------------------------------------------------------------

ifeq ($(strip $(DEVKITPRO)),)
$(error "Please set DEVKITPRO in your environment. export DEVKITPRO=<path to>devkitPro")
endif

ifeq ($(strip $(DEVKITARM)),)
$(error "Please set DEVKITARM in your environment. export DEVKITARM=<path to>devkitARM")
endif

TARGET_NAME := aniviewer3ds
BUILD       := build
TARGET      := $(BUILD)/$(TARGET_NAME)

APP_TITLE       := AniViewer 3DS Alpha
APP_DESCRIPTION := Rev6 playback-only prototype
APP_AUTHOR      := LennyFaze + Codex
APP_ICON        := $(DEVKITPRO)/libctru/default_icon.png

SOURCES := source/av6_runtime.c \
           source/raw_rev6_loader.c \
           source/main.c

OBJECTS := $(patsubst source/%.c,$(BUILD)/%.o,$(SOURCES))
DEPS    := $(OBJECTS:.o=.d)

CC       := $(DEVKITARM)/bin/arm-none-eabi-gcc
NM       := $(DEVKITARM)/bin/arm-none-eabi-gcc-nm
THREEDSX  := $(DEVKITPRO)/tools/bin/3dsxtool
SMDHTOOL := $(DEVKITPRO)/tools/bin/smdhtool

ARCH := -march=armv6k -mtune=mpcore -mfloat-abi=hard -mtp=soft

CPPFLAGS := -D__3DS__
CFLAGS   := -g -Wall -O2 -ffast-math -mword-relocations -ffunction-sections $(ARCH)
CFLAGS   += -I"$(CURDIR)/include" -I"$(BUILD)" -I"$(DEVKITPRO)/libctru/include"

LDFLAGS := -specs=3dsx.specs -g $(ARCH) -Wl,-Map,$(TARGET).map
LIBS    := -L"$(DEVKITPRO)/libctru/lib" -lcitro2d -lcitro3d -lctru -lm

ROMFS_DIR := $(CURDIR)/romfs

.PHONY: all clean

all: $(TARGET).3dsx

$(BUILD):
	@mkdir -p "$@"

$(BUILD)/%.o: source/%.c | $(BUILD)
	@echo $(notdir $<)
	@$(CC) -MMD -MP -MF "$(BUILD)/$*.d" $(CPPFLAGS) $(CFLAGS) -c "$<" -o "$@"

$(TARGET).elf: $(OBJECTS)
	@echo linking $(notdir $@)
	@$(CC) $(LDFLAGS) $(OBJECTS) $(LIBS) -o "$@"
	@$(NM) -CSn "$@" > "$(TARGET).lst"

$(TARGET).smdh: | $(BUILD)
	@$(SMDHTOOL) --create "$(APP_TITLE)" "$(APP_DESCRIPTION)" "$(APP_AUTHOR)" "$(APP_ICON)" "$@"

$(TARGET).3dsx: $(TARGET).elf $(TARGET).smdh
	@$(THREEDSX) "$<" "$@" --smdh="$(TARGET).smdh" --romfs="$(ROMFS_DIR)"
	@echo built ... $(notdir $@)

clean:
	@echo clean ...
	@rm -rf "$(BUILD)"

-include $(DEPS)
