/*
 * remotia-rdp: headless FreeRDP client rendering into shared memory.
 *
 * Renders the remote desktop (RGBA32) directly into a memory-mapped file that
 * the Remotia GUI displays, and reads a small binary input protocol on stdin
 * (mouse / keyboard / wheel / disconnect).
 *
 * Shared memory layout (offsets from the start of the mmap):
 *   0   magic   "RTDP" (4 bytes)
 *   4   width   u32
 *   8   height  u32
 *   12  stride  u32 (bytes per row)
 *   16  frame   u64 (increments on every completed frame)
 *   24  status  u32 (0=connecting 1=connected 2=disconnected 3=error)
 *   28  error   u32 (FreeRDP error code)
 *   64  framebuffer (width*height*4 RGBA)
 *
 * A single byte is written to stdout whenever the header changes so the GUI
 * knows to repaint.
 *
 * Build:
 *   cc rdp_helper.c -o rdp_helper \
 *     $(pkg-config --cflags --libs freerdp-client3 freerdp3 winpr3)
 */
#include <freerdp/freerdp.h>
#include <freerdp/client.h>
#include <freerdp/gdi/gdi.h>
#include <freerdp/input.h>
#include <freerdp/pointer.h>
#include <freerdp/client/disp.h>
#include <freerdp/channels/disp.h>
#include <freerdp/client/cliprdr.h>
#include <freerdp/channels/cliprdr.h>
#include <freerdp/client/cmdline.h>
#include <freerdp/addin.h>
#include <freerdp/constants.h>

#include <winpr/synch.h>
#include <winpr/wtypes.h>
#include <winpr/wlog.h>

#include <errno.h>
#include <fcntl.h>
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define SHM_HEADER_SIZE 64
#define SHM_MAX_FB (4096u * 2160u * 4u)

#define OFF_WIDTH 4
#define OFF_HEIGHT 8
#define OFF_STRIDE 12
#define OFF_FRAME 16
#define OFF_STATUS 24
#define OFF_ERROR 28
#define OFF_CURSOR_X 32
#define OFF_CURSOR_Y 36
#define OFF_CURSOR_VISIBLE 40

#define STATUS_CONNECTING 0
#define STATUS_CONNECTED 1
#define STATUS_DISCONNECTED 2
#define STATUS_ERROR 3

typedef struct
{
	rdpClientContext _c;
} tfContext;

static BYTE* g_shm = NULL;
static BYTE* g_fb = NULL;
static size_t g_fb_max = SHM_MAX_FB;
static rdpInput* g_input = NULL;
static DispClientContext* g_disp = NULL;
static CliprdrClientContext* g_cliprdr = NULL;
static BYTE* g_clip_data = NULL;
static UINT32 g_clip_len = 0;
static UINT16 g_req_w = 0;
static UINT16 g_req_h = 0;
static volatile int g_disconnect = 0;

static unsigned char g_stdin[4096];
static size_t g_stdin_len = 0;

static char g_self_dir[PATH_MAX];
static FREERDP_LOAD_CHANNEL_ADDIN_ENTRY_FN g_default_addin = NULL;

static PVIRTUALCHANNELENTRY my_addin_provider(LPCSTR name, LPCSTR subsystem, LPCSTR type,
                                             DWORD flags)
{
	/* Load our audio device plugin from next to the helper first, so the
	 * app bundle is self-contained; fall back to the default search path. */
	if (name && subsystem && strcmp(name, "rdpsnd") == 0 && strcmp(subsystem, "pipe") == 0)
	{
		char path[PATH_MAX];
		snprintf(path, sizeof(path), "%s/librdpsnd-client-pipe.dylib", g_self_dir);
		void* lib = dlopen(path, RTLD_NOW | RTLD_LOCAL);
		if (lib)
		{
			void* entry = dlsym(lib, "freerdp_rdpsnd_client_subsystem_entry");
			if (entry)
				return (PVIRTUALCHANNELENTRY)entry;
		}
	}
	if (g_default_addin)
		return g_default_addin(name, subsystem, type, flags);
	return NULL;
}

static void hdr_u32(size_t off, UINT32 v)
{
	memcpy(g_shm + off, &v, 4);
}

static void hdr_u64(size_t off, UINT64 v)
{
	memcpy(g_shm + off, &v, 8);
}

static UINT64 g_frame = 0;

static void notify(void)
{
	unsigned char b = 1;
	ssize_t rc = write(STDOUT_FILENO, &b, 1);
	(void)rc;
}

static UINT16 rd_u16(const unsigned char* p)
{
	return (UINT16)((p[0] << 8) | p[1]);
}

static UINT32 rd_u32(const unsigned char* p)
{
	return ((UINT32)p[0] << 24) | ((UINT32)p[1] << 16) | ((UINT32)p[2] << 8) |
	       (UINT32)p[3];
}

static BOOL tf_begin_paint(rdpContext* context)
{
	WINPR_UNUSED(context);
	return TRUE;
}

static BOOL tf_pointer_position(rdpContext* context, const POINTER_POSITION_UPDATE* p)
{
	WINPR_UNUSED(context);
	if (!p)
		return FALSE;
	hdr_u32(OFF_CURSOR_X, p->xPos);
	hdr_u32(OFF_CURSOR_Y, p->yPos);
	return TRUE;
}

static BOOL tf_pointer_system(rdpContext* context, const POINTER_SYSTEM_UPDATE* p)
{
	WINPR_UNUSED(context);
	if (!p)
		return FALSE;
	hdr_u32(OFF_CURSOR_VISIBLE, (p->type == SYSPTR_NULL) ? 0 : 1);
	return TRUE;
}

static BOOL tf_end_paint(rdpContext* context)
{
	rdpGdi* gdi = context->gdi;
	if (!gdi)
		return TRUE;

	hdr_u32(OFF_WIDTH, (UINT32)gdi->width);
	hdr_u32(OFF_HEIGHT, (UINT32)gdi->height);
	hdr_u32(OFF_STRIDE, gdi->stride);
	g_frame++;
	hdr_u64(OFF_FRAME, g_frame);
	notify();
	return TRUE;
}

static BOOL tf_desktop_resize(rdpContext* context)
{
	rdpGdi* gdi = context->gdi;
	rdpSettings* settings = context->settings;
	UINT32 w = freerdp_settings_get_uint32(settings, FreeRDP_DesktopWidth);
	UINT32 h = freerdp_settings_get_uint32(settings, FreeRDP_DesktopHeight);

	if (!gdi || !w || !h)
		return TRUE;

	if ((UINT64)w * h * 4 <= g_fb_max)
		gdi_resize_ex(gdi, w, h, w * 4, PIXEL_FORMAT_RGBA32, g_fb, NULL);
	else
		gdi_resize(gdi, w, h);

	hdr_u32(OFF_WIDTH, w);
	hdr_u32(OFF_HEIGHT, h);
	hdr_u32(OFF_STRIDE, w * 4);
	g_frame++;
	hdr_u64(OFF_FRAME, g_frame);
	notify();
	return TRUE;
}

static void send_monitor_layout(UINT16 w, UINT16 h)
{
	if (!g_disp || !g_disp->SendMonitorLayout || !w || !h)
		return;

	DISPLAY_CONTROL_MONITOR_LAYOUT layout = { 0 };
	layout.Flags = DISPLAY_CONTROL_MONITOR_PRIMARY;
	layout.Width = w;
	layout.Height = h;
	layout.PhysicalWidth = w;
	layout.PhysicalHeight = h;
	layout.Orientation = ORIENTATION_LANDSCAPE;
	layout.DesktopScaleFactor = 0;
	layout.DeviceScaleFactor = 0;
	g_disp->SendMonitorLayout(g_disp, 1, &layout);
}

/* --- clipboard (cliprdr) ------------------------------------------------- */
#define CF_UNICODETEXT 13

static void stdout_clipboard(const BYTE* data, UINT32 len)
{
	unsigned char hdr[5];
	hdr[0] = 0x02;
	hdr[1] = (len >> 24) & 0xFF;
	hdr[2] = (len >> 16) & 0xFF;
	hdr[3] = (len >> 8) & 0xFF;
	hdr[4] = len & 0xFF;
	if (write(STDOUT_FILENO, hdr, 5) != 5)
		return;
	if (len > 0)
		(void)write(STDOUT_FILENO, data, len);
}

static UINT cliprdr_announce_formats(void)
{
	CLIPRDR_FORMAT format = { CF_UNICODETEXT, NULL };
	CLIPRDR_FORMAT_LIST fl = { 0 };

	if (!g_cliprdr || !g_clip_data || !g_cliprdr->ClientFormatList)
		return CHANNEL_RC_OK;

	fl.common.msgType = CB_FORMAT_LIST;
	fl.common.msgFlags = 0;
	fl.numFormats = 1;
	fl.formats = &format;
	return g_cliprdr->ClientFormatList(g_cliprdr, &fl);
}

static UINT cliprdr_server_caps(CliprdrClientContext* c, const CLIPRDR_CAPABILITIES* caps)
{
	WINPR_UNUSED(c);
	WINPR_UNUSED(caps);
	return CHANNEL_RC_OK;
}

static UINT cliprdr_monitor_ready(CliprdrClientContext* c, const CLIPRDR_MONITOR_READY* m)
{
	WINPR_UNUSED(c);
	WINPR_UNUSED(m);
	return cliprdr_announce_formats();
}

static UINT cliprdr_server_format_list(CliprdrClientContext* c, const CLIPRDR_FORMAT_LIST* fl)
{
	UINT32 i;
	for (i = 0; i < fl->numFormats; i++)
	{
		if (fl->formats[i].formatId == CF_UNICODETEXT)
		{
			CLIPRDR_FORMAT_DATA_REQUEST req = { 0 };
			req.common.msgType = CB_FORMAT_DATA_REQUEST;
			req.common.msgFlags = 0;
			req.requestedFormatId = CF_UNICODETEXT;
			return c->ClientFormatDataRequest(c, &req);
		}
	}
	return CHANNEL_RC_OK;
}

static UINT cliprdr_server_data_request(CliprdrClientContext* c,
                                        const CLIPRDR_FORMAT_DATA_REQUEST* req)
{
	CLIPRDR_FORMAT_DATA_RESPONSE resp = { 0 };
	resp.common.msgType = CB_FORMAT_DATA_RESPONSE;
	if (req->requestedFormatId == CF_UNICODETEXT && g_clip_data)
	{
		resp.common.msgFlags = CB_RESPONSE_OK;
		resp.common.dataLen = g_clip_len;
		resp.requestedFormatData = g_clip_data;
	}
	else
	{
		resp.common.msgFlags = CB_RESPONSE_FAIL;
	}
	return c->ClientFormatDataResponse(c, &resp);
}

static UINT cliprdr_server_data_response(CliprdrClientContext* c,
                                         const CLIPRDR_FORMAT_DATA_RESPONSE* resp)
{
	WINPR_UNUSED(c);
	if (resp->requestedFormatData && resp->common.dataLen > 0)
		stdout_clipboard(resp->requestedFormatData, resp->common.dataLen);
	return CHANNEL_RC_OK;
}

static void cliprdr_attach(CliprdrClientContext* clip)
{
	CLIPRDR_GENERAL_CAPABILITY_SET gen = { 0 };
	CLIPRDR_CAPABILITIES caps = { 0 };

	g_cliprdr = clip;
	fprintf(stderr, "[helper] clipboard channel attached\n");
	clip->ServerCapabilities = cliprdr_server_caps;
	clip->MonitorReady = cliprdr_monitor_ready;
	clip->ServerFormatList = cliprdr_server_format_list;
	clip->ServerFormatDataRequest = cliprdr_server_data_request;
	clip->ServerFormatDataResponse = cliprdr_server_data_response;

	gen.capabilitySetType = CB_CAPSTYPE_GENERAL;
	gen.capabilitySetLength = CB_CAPSTYPE_GENERAL_LEN;
	gen.version = CB_CAPS_VERSION_2;
	gen.generalFlags = CB_USE_LONG_FORMAT_NAMES;
	caps.common.msgType = CB_CLIP_CAPS;
	caps.common.msgFlags = 0;
	caps.cCapabilitiesSets = 1;
	caps.capabilitySets = (CLIPRDR_CAPABILITY_SET*)&gen;
	if (clip->ClientCapabilities)
		clip->ClientCapabilities(clip, &caps);
}

static void tf_on_channel_connected(void* context, const ChannelConnectedEventArgs* e)
{
	if (!e || !e->name)
		return;
	if (strcmp(e->name, DISP_DVC_CHANNEL_NAME) == 0)
	{
		g_disp = (DispClientContext*)e->pInterface;
		/* Apply any resize that was requested before the channel was ready. */
		send_monitor_layout(g_req_w, g_req_h);
	}
	else if (strcmp(e->name, CLIPRDR_SVC_CHANNEL_NAME) == 0)
	{
		cliprdr_attach((CliprdrClientContext*)e->pInterface);
	}
	else if (strcmp(e->name, "rdpdr") == 0)
	{
		fprintf(stderr, "[helper] device-redirection channel attached (shared folder)\n");
	}
	else
	{
		freerdp_client_OnChannelConnectedEventHandler(context, e);
	}
}

static BOOL tf_pre_connect(freerdp* instance)
{
	rdpSettings* settings = instance->context->settings;
	freerdp_settings_set_bool(settings, FreeRDP_AutoAcceptCertificate, TRUE);
	freerdp_settings_set_uint32(settings, FreeRDP_OsMajorType, 0);
	freerdp_settings_set_uint32(settings, FreeRDP_OsMinorType, 0);
	PubSub_SubscribeChannelConnected(instance->context->pubSub, tf_on_channel_connected);
	return TRUE;
}

static BOOL tf_post_connect(freerdp* instance)
{
	rdpSettings* settings = instance->context->settings;
	UINT32 w = freerdp_settings_get_uint32(settings, FreeRDP_DesktopWidth);
	UINT32 h = freerdp_settings_get_uint32(settings, FreeRDP_DesktopHeight);
	UINT32 stride = w * 4;

	if (!w || !h)
		return FALSE;

	if ((UINT64)w * h * 4 > g_fb_max)
		return FALSE;

	if (!gdi_init_ex(instance, PIXEL_FORMAT_RGBA32, stride, g_fb, NULL))
		return FALSE;

	instance->context->update->BeginPaint = tf_begin_paint;
	instance->context->update->EndPaint = tf_end_paint;
	instance->context->update->DesktopResize = tf_desktop_resize;
	instance->context->update->pointer->PointerPosition = tf_pointer_position;
	instance->context->update->pointer->PointerSystem = tf_pointer_system;

	g_input = instance->context->input;
	if (g_input)
	{
		/* Standard RDP input setup: sync keyboard state and take focus. */
		freerdp_input_send_synchronize_event(g_input, 0);
		freerdp_input_send_focus_in_event(g_input, 0);
	}

	hdr_u32(OFF_WIDTH, w);
	hdr_u32(OFF_HEIGHT, h);
	hdr_u32(OFF_STRIDE, stride);
	hdr_u32(OFF_STATUS, STATUS_CONNECTED);
	g_frame++;
	hdr_u64(OFF_FRAME, g_frame);
	notify();
	return TRUE;
}

static void tf_post_disconnect(freerdp* instance)
{
	gdi_free(instance);
	hdr_u32(OFF_STATUS, STATUS_DISCONNECTED);
	notify();
}

static BOOL tf_client_new(freerdp* instance, rdpContext* context)
{
	instance->PreConnect = tf_pre_connect;
	instance->PostConnect = tf_post_connect;
	instance->PostDisconnect = tf_post_disconnect;
	WINPR_UNUSED(context);
	return TRUE;
}

static void tf_client_free(freerdp* instance, rdpContext* context)
{
	WINPR_UNUSED(instance);
	WINPR_UNUSED(context);
}

static int tf_client_start(rdpContext* context)
{
	WINPR_UNUSED(context);
	return 0;
}

static int tf_client_stop(rdpContext* context)
{
	WINPR_UNUSED(context);
	return 0;
}

static int RdpClientEntry(RDP_CLIENT_ENTRY_POINTS* p)
{
	memset(p, 0, sizeof(*p));
	p->Version = RDP_CLIENT_INTERFACE_VERSION;
	p->Size = sizeof(RDP_CLIENT_ENTRY_POINTS_V1);
	p->ContextSize = sizeof(tfContext);
	p->ClientNew = tf_client_new;
	p->ClientFree = tf_client_free;
	p->ClientStart = tf_client_start;
	p->ClientStop = tf_client_stop;
	return 0;
}

static void handle_message(const unsigned char* m)
{
	switch (m[0])
	{
		case 'M': /* mouse: flags u16, x u16, y u16 */
			if (g_input)
				freerdp_input_send_mouse_event(g_input, rd_u16(m + 1), rd_u16(m + 3),
				                               rd_u16(m + 5));
			break;
		case 'K': /* keyboard: rdp_scancode u32, down u8 */
			if (g_input)
				freerdp_input_send_keyboard_event_ex(g_input, m[5] ? TRUE : FALSE, FALSE,
				                                     rd_u32(m + 1));
			break;
		case 'U': /* unicode: code u16, down u8 */
			if (g_input)
				freerdp_input_send_unicode_keyboard_event(g_input,
				                                          m[3] ? 0 : KBD_FLAGS_RELEASE,
				                                          rd_u16(m + 1));
			break;
		case 'X': /* disconnect */
			g_disconnect = 1;
			break;
		case 'R': /* resize: width u16, height u16 */
			g_req_w = rd_u16(m + 1);
			g_req_h = rd_u16(m + 3);
			send_monitor_layout(g_req_w, g_req_h);
			break;
		case 'C': /* clipboard: u32 length + UTF-16LE data */
			g_clip_len = rd_u32(m + 1);
			free(g_clip_data);
			g_clip_data = (BYTE*)malloc(g_clip_len ? g_clip_len : 1);
			if (g_clip_data && g_clip_len)
				memcpy(g_clip_data, m + 5, g_clip_len);
			cliprdr_announce_formats();
			break;
		default:
			break;
	}
}

static void process_stdin(void)
{
	unsigned char buf[256];
	ssize_t n;

	for (;;)
	{
		n = read(STDIN_FILENO, buf, sizeof(buf));
		if (n <= 0)
			break; /* EAGAIN (non-blocking) or EOF */
		for (ssize_t i = 0; i < n; i++)
		{
			if (g_stdin_len >= sizeof(g_stdin))
				g_stdin_len = 0; /* defensive: drop and resync */
			g_stdin[g_stdin_len++] = buf[i];
		}
	}

	for (;;)
	{
		size_t need = 0;
		if (g_stdin_len < 1)
			break;
		switch (g_stdin[0])
		{
			case 'M':
				need = 7;
				break;
			case 'K':
				need = 6;
				break;
			case 'U':
				need = 4;
				break;
			case 'R':
				need = 5;
				break;
			case 'X':
				need = 1;
				break;
			case 'C': /* variable length: type + u32 len + data */
				if (g_stdin_len >= 5)
					need = 5 + (size_t)rd_u32(g_stdin + 1);
				break;
			default:
				memmove(g_stdin, g_stdin + 1, g_stdin_len - 1);
				g_stdin_len--;
				continue;
		}
		if (need == 0 || g_stdin_len < need)
			break;
		handle_message(g_stdin);
		memmove(g_stdin, g_stdin + need, g_stdin_len - need);
		g_stdin_len -= need;
	}
}

static DWORD WINAPI tf_thread(LPVOID arg)
{
	freerdp* instance = (freerdp*)arg;
	rdpContext* context = instance->context;
	DWORD result = 0;

	if (!freerdp_connect(instance))
	{
		result = freerdp_get_last_error(context);
		hdr_u32(OFF_STATUS, STATUS_ERROR);
		hdr_u32(OFF_ERROR, result);
		notify();
		fprintf(stderr, "connection failure: 0x%08x\n", result);
		goto out;
	}

	while (!freerdp_shall_disconnect_context(context) && !g_disconnect)
	{
		HANDLE handles[64];
		DWORD nCount = freerdp_get_event_handles(context, handles, 64);
		DWORD status;

		if (nCount == 0)
			break;

		status = WaitForMultipleObjects(nCount, handles, FALSE, 50);
		if (status == WAIT_FAILED)
			break;

		if (status != WAIT_TIMEOUT && !freerdp_check_event_handles(context))
			break;

		process_stdin();
	}

	freerdp_disconnect(instance);
out:
	return result;
}

int main(int argc, char* argv[])
{
	RDP_CLIENT_ENTRY_POINTS entry = { 0 };
	rdpContext* context;
	tfContext* tf;
	rdpSettings* settings;
	const char* shm_path;
	size_t shm_size;
	int fd;
	int rc = 1;

	if (argc < 8)
	{
		fprintf(stderr,
		        "usage: %s host port username password width height shm_path\n",
		        argv[0]);
		return 2;
	}

	shm_path = argv[7];
	shm_size = SHM_HEADER_SIZE + SHM_MAX_FB;

	fd = open(shm_path, O_RDWR | O_CREAT, 0600);
	if (fd < 0)
	{
		perror("open shm");
		return 2;
	}
	if (ftruncate(fd, (off_t)shm_size) != 0)
	{
		perror("ftruncate");
		close(fd);
		return 2;
	}
	g_shm = mmap(NULL, shm_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	close(fd);
	if (g_shm == MAP_FAILED)
	{
		perror("mmap");
		return 2;
	}
	g_fb = g_shm + SHM_HEADER_SIZE;
	memcpy(g_shm, "RTDP", 4);
	hdr_u32(OFF_STATUS, STATUS_CONNECTING);
	hdr_u32(OFF_WIDTH, (UINT32)atoi(argv[5]));
	hdr_u32(OFF_HEIGHT, (UINT32)atoi(argv[6]));
	hdr_u32(OFF_STRIDE, (UINT32)atoi(argv[5]) * 4);

	/* Non-blocking stdin for input events. */
	{
		int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
		fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK);
	}
	setvbuf(stdout, NULL, _IONBF, 0);
	/* Keep stdout clean for our protocol (frame/clipboard notifications) by
	 * silencing FreeRDP's logger, which writes to stdout on this platform. */
	WLog_SetLogLevel(WLog_GetRoot(), WLOG_OFF);

	/* Locate the audio plugin relative to this binary (for the app bundle). */
	{
		char resolved[PATH_MAX];
		if (realpath(argv[0], resolved))
		{
			char* slash = strrchr(resolved, '/');
			if (slash)
			{
				*slash = '\0';
				snprintf(g_self_dir, sizeof(g_self_dir), "%s", resolved);
			}
		}
		g_default_addin = freerdp_get_current_addin_provider();
		freerdp_register_addin_provider(my_addin_provider, 0);
	}

	RdpClientEntry(&entry);
	context = freerdp_client_context_new(&entry);
	if (!context)
		goto fail;
	tf = (tfContext*)context;
	WINPR_UNUSED(tf);

	settings = context->settings;
	freerdp_settings_set_string(settings, FreeRDP_ServerHostname, argv[1]);
	freerdp_settings_set_uint32(settings, FreeRDP_ServerPort, (UINT32)atoi(argv[2]));
	freerdp_settings_set_string(settings, FreeRDP_Username, argv[3]);
	freerdp_settings_set_string(settings, FreeRDP_Password, argv[4]);
	freerdp_settings_set_uint32(settings, FreeRDP_DesktopWidth, (UINT32)atoi(argv[5]));
	freerdp_settings_set_uint32(settings, FreeRDP_DesktopHeight, (UINT32)atoi(argv[6]));
	freerdp_settings_set_uint32(settings, FreeRDP_ColorDepth, 32);
	freerdp_settings_set_bool(settings, FreeRDP_AutoAcceptCertificate, TRUE);

	/* Optional shared folder (drive redirection): argv[8] = local path. */
	if (argc >= 9 && argv[8] && argv[8][0] != '\0')
	{
		const char* params[3] = { "drive", "Remotia", argv[8] };
		freerdp_client_add_device_channel(settings, 3, params);
	}

	/* Audio playback: use our "pipe" device which streams PCM to the app. */
	freerdp_settings_set_bool(settings, FreeRDP_AudioPlayback, TRUE);
	{
		const char* snd[2] = { "rdpsnd", "sys:pipe" };
		freerdp_client_add_static_channel(settings, 2, snd);
	}

	if (freerdp_client_start(context) != 0)
		goto fail;

	rc = (int)tf_thread(context->instance);

	freerdp_client_stop(context);
fail:
	freerdp_client_context_free(context);
	munmap(g_shm, shm_size);
	return rc;
}
