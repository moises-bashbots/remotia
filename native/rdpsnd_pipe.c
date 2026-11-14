/*
 * Remotia rdpsnd "pipe" device: receives remote audio (PCM) and streams it to
 * the Remotia app via stdout so Qt can play it locally.
 *
 * Build (produces librdpsnd-client-pipe.dylib):
 *   cc -dynamiclib -o librdpsnd-client-pipe.dylib rdpsnd_pipe.c \
 *     -I$(brew --prefix freerdp)/include/freerdp3 -I$(brew --prefix freerdp)/include/winpr3
 *
 * stdout protocol (shared with the frame/clipboard notifications):
 *   0x03 + u16 formatTag + u16 channels + u32 sampleRate + u16 bitsPerSample
 *   0x04 + u32 length + PCM bytes
 */
#include <freerdp/client/rdpsnd.h>
#include <freerdp/codec/audio.h>

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

typedef struct
{
	rdpsndDevicePlugin device;
} pipe_device_t;

static BOOL pipe_format_supported(rdpsndDevicePlugin* device, const AUDIO_FORMAT* format)
{
	WINPR_UNUSED(device);
	/* Accept the common PCM-ish formats; Qt can handle PCM and float. */
	return (format->wFormatTag == WAVE_FORMAT_PCM || format->wFormatTag == WAVE_FORMAT_IEEE_FLOAT ||
	        format->wFormatTag == WAVE_FORMAT_ALAW || format->wFormatTag == WAVE_FORMAT_MULAW);
}

static BOOL pipe_open(rdpsndDevicePlugin* device, const AUDIO_FORMAT* format, UINT32 latency)
{
	unsigned char hdr[11];
	WINPR_UNUSED(device);
	WINPR_UNUSED(latency);

	hdr[0] = 0x03;
	hdr[1] = format->wFormatTag & 0xFF;
	hdr[2] = (format->wFormatTag >> 8) & 0xFF;
	hdr[3] = format->nChannels & 0xFF;
	hdr[4] = (format->nChannels >> 8) & 0xFF;
	hdr[5] = (format->nSamplesPerSec >> 24) & 0xFF;
	hdr[6] = (format->nSamplesPerSec >> 16) & 0xFF;
	hdr[7] = (format->nSamplesPerSec >> 8) & 0xFF;
	hdr[8] = format->nSamplesPerSec & 0xFF;
	hdr[9] = format->wBitsPerSample & 0xFF;
	hdr[10] = (format->wBitsPerSample >> 8) & 0xFF;
	if (write(STDOUT_FILENO, hdr, sizeof(hdr)) != (ssize_t)sizeof(hdr))
		return FALSE;
	return TRUE;
}

static UINT pipe_play_ex(rdpsndDevicePlugin* device, const AUDIO_FORMAT* format, const BYTE* data,
                         size_t size)
{
	unsigned char hdr[5];
	WINPR_UNUSED(device);
	WINPR_UNUSED(format);

	hdr[0] = 0x04;
	hdr[1] = (size >> 24) & 0xFF;
	hdr[2] = (size >> 16) & 0xFF;
	hdr[3] = (size >> 8) & 0xFF;
	hdr[4] = size & 0xFF;
	if (write(STDOUT_FILENO, hdr, sizeof(hdr)) != (ssize_t)sizeof(hdr))
		return 1;
	if (size > 0 && write(STDOUT_FILENO, data, size) != (ssize_t)size)
		return 1;
	return 0;
}

static void pipe_close(rdpsndDevicePlugin* device)
{
	WINPR_UNUSED(device);
}

static void pipe_free(rdpsndDevicePlugin* device)
{
	free(device);
}

UINT freerdp_rdpsnd_client_subsystem_entry(PFREERDP_RDPSND_DEVICE_ENTRY_POINTS pEntryPoints)
{
	pipe_device_t* dev = (pipe_device_t*)calloc(1, sizeof(pipe_device_t));
	if (!dev)
		return 1;

	dev->device.FormatSupported = pipe_format_supported;
	dev->device.Open = pipe_open;
	dev->device.PlayEx = pipe_play_ex;
	dev->device.Close = pipe_close;
	dev->device.Free = pipe_free;

	pEntryPoints->pRegisterRdpsndDevice(pEntryPoints->rdpsnd, &dev->device);
	fprintf(stderr, "[audio] pipe device registered\n");
	return 0;
}
