// Check the live pose without creating a compositor or touching desktop input.
#define XR_USE_TIMESPEC
#include <time.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    long wait_seconds = 15;
    if (argc != 1) {
        char *end = NULL;
        if (argc != 3 || strcmp(argv[1], "--wait") != 0) return 2;
        wait_seconds = strtol(argv[2], &end, 10);
        if (!end || *end || wait_seconds < 0 || wait_seconds > 120) return 2;
    }
    XrInstance instance = XR_NULL_HANDLE;
    XrSession session = XR_NULL_HANDLE;
    XrSpace space = XR_NULL_HANDLE;
    int status = 1;
    XrResult result;
#define CHECK(call) do { result = (call); if (XR_FAILED(result)) { \
    fprintf(stderr, "OpenXR readiness: %s failed (%d).\n", #call, result); \
    goto cleanup; } } while (0)
    const char *extensions[] = {XR_MND_HEADLESS_EXTENSION_NAME,
                               XR_KHR_CONVERT_TIMESPEC_TIME_EXTENSION_NAME};
    XrInstanceCreateInfo create = {.type = XR_TYPE_INSTANCE_CREATE_INFO,
        .applicationInfo = {.applicationName = "DCS tracking readiness",
                            .apiVersion = XR_CURRENT_API_VERSION},
        .enabledExtensionCount = 2, .enabledExtensionNames = extensions};
    CHECK(xrCreateInstance(&create, &instance));
    XrSystemGetInfo system_info = {.type = XR_TYPE_SYSTEM_GET_INFO,
                                 .formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY};
    XrSystemId system;
    CHECK(xrGetSystem(instance, &system_info, &system));
    XrSessionCreateInfo session_info = {.type = XR_TYPE_SESSION_CREATE_INFO, .systemId = system};
    CHECK(xrCreateSession(instance, &session_info, &session));
    XrSessionBeginInfo begin = {.type = XR_TYPE_SESSION_BEGIN_INFO,
                               .primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO};
    CHECK(xrBeginSession(session, &begin));
    XrReferenceSpaceCreateInfo space_info = {.type = XR_TYPE_REFERENCE_SPACE_CREATE_INFO,
        .referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW,
        .poseInReferenceSpace = {.orientation = {.w = 1}}};
    CHECK(xrCreateReferenceSpace(session, &space_info, &space));
    PFN_xrConvertTimespecTimeToTimeKHR convert;
    CHECK(xrGetInstanceProcAddr(instance, "xrConvertTimespecTimeToTimeKHR", (PFN_xrVoidFunction *)&convert));
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    const time_t deadline = now.tv_sec + wait_seconds;
    const XrViewStateFlags required = XR_VIEW_STATE_POSITION_VALID_BIT | XR_VIEW_STATE_ORIENTATION_VALID_BIT;
    for (;;) {
        clock_gettime(CLOCK_MONOTONIC, &now);
        XrTime xr_time;
        CHECK(convert(instance, &now, &xr_time));
        XrViewLocateInfo locate = {.type = XR_TYPE_VIEW_LOCATE_INFO,
            .viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO,
            .displayTime = xr_time, .space = space};
        XrViewState state = {.type = XR_TYPE_VIEW_STATE};
        XrView views[2] = {{.type = XR_TYPE_VIEW}, {.type = XR_TYPE_VIEW}};
        uint32_t count;
        CHECK(xrLocateViews(session, &locate, &state, 2, &count, views));
        if (count == 2 && (state.viewStateFlags & required) == required) {
            puts("Lighthouse tracking ready: headset position and orientation are valid.");
            status = 0;
            break;
        }
        if (now.tv_sec >= deadline) {
            fprintf(stderr, "Headset tracking unavailable (view flags=0x%llx). Power on the Lighthouse base stations and place the Beyond in view of them.\n",
                    (unsigned long long)state.viewStateFlags);
            status = 3;
            break;
        }
        nanosleep(&(struct timespec){.tv_nsec = 200000000}, NULL);
    }
cleanup:
    if (space != XR_NULL_HANDLE) xrDestroySpace(space);
    if (session != XR_NULL_HANDLE) xrDestroySession(session);
    if (instance != XR_NULL_HANDLE) xrDestroyInstance(instance);
    return status;
}
