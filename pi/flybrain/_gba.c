/* A Game Boy Advance, embedded.
 *
 * Wraps libmgba so the emulator runs inside the flybrain process: frames come
 * straight out of memory and buttons go straight into the core. The earlier
 * approach -- screen-grabbing an mGBA window and typing at it with xdotool --
 * could not work on a small display where every app is a maximised window in
 * the same spot: capture saw whatever happened to be on top, and synthetic
 * keys went to whatever had focus, which could be a terminal.
 *
 * flags.h must come first. It records how the library was built, and several
 * of those flags (USE_DEBUGGERS among them) add fields to struct mCore; get
 * them wrong and every function pointer after that point is at the wrong
 * offset.
 */
#include <mgba/flags.h>
#include <mgba/core/core.h>
#include <mgba/core/config.h>
#include <mgba/core/log.h>
#include <mgba/core/serialize.h>
#include <mgba/core/blip_buf.h>
#include <mgba-util/vfs.h>

#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>

static struct mCore *core;
static color_t *video;
static unsigned width, height;
static double audio_ms;          /* mean square of the last frame's samples */
/* The last frame's sound, kept so it can be played -- the stream to Twitch
 * needs it. Left and right are separate blip channels, read one at a time. */
static short audio_lr[2][4096];
static int audio_n;              /* stereo frames in audio_lr */

static void quiet(struct mLogger *l, int cat, enum mLogLevel lvl,
                  const char *fmt, va_list args)
{
    (void)l; (void)cat; (void)lvl; (void)fmt; (void)args;
}
static struct mLogger logger = { .log = quiet, .filter = NULL };

int gba_bytes_per_pixel(void) { return BYTES_PER_PIXEL; }
int gba_width(void)  { return (int)width; }
int gba_height(void) { return (int)height; }

/* 0 on success. rom required; sav and state may be NULL. */
int gba_open(const char *rom, const char *sav, const char *state)
{
    mLogSetDefaultLogger(&logger);
    core = mCoreFind(rom);
    if (!core) return -1;
    if (!core->init(core)) return -2;
    mCoreInitConfig(core, NULL);

    core->desiredVideoDimensions(core, &width, &height);
    video = calloc((size_t)width * height, BYTES_PER_PIXEL);
    if (!video) return -3;
    core->setVideoBuffer(core, video, width);
    core->setAudioBufferSize(core, 2048);

    if (!mCoreLoadFile(core, rom)) return -4;
    if (sav) {
        struct VFile *vf = VFileOpen(sav, O_CREAT | O_RDWR);
        if (!vf || !core->loadSave(core, vf)) return -5;
    }
    core->reset(core);

    if (state) {
        struct VFile *vf = VFileOpen(state, O_RDONLY);
        if (!vf) return -6;
        /* Core state only. Not SAVESTATE_SAVEDATA: that would overwrite the
         * battery save with whatever was inside the savestate. */
        bool ok = mCoreLoadStateNamed(core, vf, 0);
        vf->close(vf);
        if (!ok) return -7;
    }
    return 0;
}

/* Advance one frame with the given GBA key bitmask; returns the framebuffer. */
color_t *gba_frame(uint32_t keys)
{
    core->setKeys(core, keys);
    core->runFrame(core);
    /* Drain the audio every frame -- nothing plays it, but the sample buffers
     * must not fill -- and keep its loudness, which becomes the fly's ear. */
    double acc = 0.0;
    int total = 0;
    int got_ch[2] = {0, 0};
    for (int ch = 0; ch < 2; ch++) {
        struct blip_t *b = core->getAudioChannel(core, ch);
        int avail = blip_samples_avail(b);
        if (avail > 4096) avail = 4096;
        int got = blip_read_samples(b, audio_lr[ch], avail, 0);
        for (int i = 0; i < got; i++)
            acc += (double)audio_lr[ch][i] * audio_lr[ch][i];
        total += got;
        got_ch[ch] = got;
        blip_clear(b);
    }
    audio_n = got_ch[0] < got_ch[1] ? got_ch[0] : got_ch[1];
    audio_ms = total ? acc / total : 0.0;
    return video;
}

double gba_audio_rms(void) { return audio_ms > 0 ? __builtin_sqrt(audio_ms) : 0.0; }

/* The last frame's sound as interleaved 16-bit stereo, at most `max_frames`
 * frames; returns how many were written. */
int gba_audio_samples(short *out, int max_frames)
{
    int n = audio_n < max_frames ? audio_n : max_frames;
    for (int i = 0; i < n; i++) {
        out[2 * i] = audio_lr[0][i];
        out[2 * i + 1] = audio_lr[1][i];
    }
    return n;
}

/* Direct bus reads. Used for proprioception -- "did the player actually
 * move" -- not for vision, which comes only from the rendered frame. */
uint32_t gba_read32(uint32_t addr) { return core->busRead32(core, addr); }
uint16_t gba_read16(uint32_t addr) { return core->busRead16(core, addr); }
uint8_t  gba_read8(uint32_t addr)  { return core->busRead8(core, addr); }

/* Whole-machine snapshots for resuming a game across sessions.
 *
 * Includes the cartridge's battery save memory (SAVESTATE_SAVEDATA) and clock,
 * so a snapshot is self-contained: loading it restores exactly the moment it
 * was taken, mid-battle or mid-dialogue included -- which an in-game save
 * cannot do. Writing atomically is the caller's job (see game.GBA.save_state). */
int gba_save_state(const char *path)
{
    /* Read-write, not write-only: mGBA serialises by memory-mapping the file,
     * and a shared writable mapping needs read access too. */
    struct VFile *vf = VFileOpen(path, O_CREAT | O_TRUNC | O_RDWR);
    if (!vf) return -1;
    bool ok = mCoreSaveStateNamed(core, vf,
                                  SAVESTATE_SAVEDATA | SAVESTATE_RTC | SAVESTATE_METADATA);
    vf->close(vf);
    return ok ? 0 : -2;
}

int gba_load_state(const char *path)
{
    struct VFile *vf = VFileOpen(path, O_RDONLY);
    if (!vf) return -1;
    bool ok = mCoreLoadStateNamed(core, vf, SAVESTATE_SAVEDATA | SAVESTATE_RTC);
    vf->close(vf);
    return ok ? 0 : -2;
}

uint32_t gba_frame_counter(void) { return core->frameCounter(core); }

void gba_close(void)
{
    if (core) {
        core->deinit(core);
        core = NULL;
    }
    free(video);
    video = NULL;
}
