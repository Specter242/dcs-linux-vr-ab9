// Exercise the exact original and patched QVF DXBC shaders through D3D11.
// Build with MinGW; run under the same GE-Proton/DXVK path used by DCS.
#include <d3d11.h>
#include <d3dcompiler.h>
#include <wincrypt.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

template<typename T> struct Com {
    T* p = nullptr;
    ~Com() { if (p) p->Release(); }
    T** out() { return &p; }
    T* operator->() { return p; }
};

static void check(HRESULT hr, const char* operation) {
    if (FAILED(hr)) {
        std::printf("FAIL %s: %08lx\n", operation, hr);
        throw std::runtime_error(operation);
    }
}

static std::vector<uint8_t> read(const char* path) {
    std::ifstream file(path, std::ios::binary);
    std::vector<uint8_t> data{std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
    if (data.empty()) throw std::runtime_error("Empty shader input");
    return data;
}

static std::string hash(const std::vector<uint8_t>& data) {
    HCRYPTPROV provider = 0;
    HCRYPTHASH digest = 0;
    if (!CryptAcquireContextW(&provider, nullptr, nullptr, PROV_RSA_AES, CRYPT_VERIFYCONTEXT))
        throw std::runtime_error("Acquire SHA256 provider");
    bool okay = CryptCreateHash(provider, CALG_SHA_256, 0, 0, &digest) &&
                CryptHashData(digest, data.data(), DWORD(data.size()), 0);
    uint8_t bytes[32]; DWORD size = sizeof(bytes);
    okay = okay && CryptGetHashParam(digest, HP_HASHVAL, bytes, &size, 0);
    if (digest) CryptDestroyHash(digest);
    CryptReleaseContext(provider, 0);
    if (!okay || size != 32) throw std::runtime_error("Hash shader");
    std::string result;
    for (auto byte : bytes) {
        char pair[3]; std::snprintf(pair, sizeof(pair), "%02x", byte); result += pair;
    }
    return result;
}

static float ramp(float v) {
    v = std::clamp(v, 0.0f, 1.0f);
    return v * v * (3.0f - 2.0f * v);
}

static float coverage(float u, float v, float smoothing, bool original) {
    if (u <= 0 || u >= 1 || v <= 0 || v >= 1) return 0;
    if (smoothing == 0) return 1;
    float sx = ramp(u / smoothing) - ramp((u - 1 + smoothing) / smoothing);
    float sy = ramp(v / smoothing) - ramp((v - 1 + smoothing) / smoothing);
    return std::max(original ? .5f : 0.0f, sx * sy);
}

static float decode(float v) {
    return v <= .04045f ? v / 12.92f : std::pow((v + .055f) / 1.055f, 2.4f);
}

static float encode(float v) {
    return v <= .0031308f ? 12.92f * v : 1.055f * std::pow(v, 1 / 2.4f) - .055f;
}

int main(int argc, char** argv) {
    if (argc != 4) { std::puts("Usage: test_qvf_feather.exe original.dxbc continuous.dxbc report.log"); return 2; }
    // Proton's GUI executable launcher may not forward the child's console.
    if (!std::freopen(argv[3], "w", stdout)) return 2;
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    try {
        auto original = read(argv[1]), continuous = read(argv[2]);
        Com<ID3D11Device> device;
        Com<ID3D11DeviceContext> context;
        check(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
              nullptr, 0, D3D11_SDK_VERSION, device.out(), nullptr, context.out()), "CreateDevice");
        const char* vertexSource = R"(
struct Output { float4 pos:SV_POSITION; float2 uv:PROJ_COORD0; float3 focus:PROJ_COORD1; };
Output main(uint id:SV_VertexID) {
    Output o;
    float2 uv=float2((id<<1)&2,id&2);
    o.pos=float4(uv*float2(2,-2)+float2(-1,1),0,1);
    o.uv=uv;
    o.focus=float3((uv*1.5-.25)*float2(2,-2)+float2(-1,1),1);
    return o;
})";
        Com<ID3DBlob> vertexBlob, errors;
        HRESULT hr = D3DCompile(vertexSource, std::char_traits<char>::length(vertexSource),
              "feather-probe", nullptr, nullptr, "main", "vs_5_0", 0, 0, vertexBlob.out(), errors.out());
        if (FAILED(hr) && errors.p) std::printf("%.*s\n", int(errors->GetBufferSize()), (char*)errors->GetBufferPointer());
        check(hr, "CompileVertexShader");
        Com<ID3D11VertexShader> vertex;
        check(device->CreateVertexShader(vertexBlob->GetBufferPointer(), vertexBlob->GetBufferSize(), nullptr, vertex.out()), "CreateVertexShader");
        context->VSSetShader(vertex.p, nullptr, 0);
        context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        D3D11_RASTERIZER_DESC rasterDesc = {};
        rasterDesc.FillMode = D3D11_FILL_SOLID; rasterDesc.CullMode = D3D11_CULL_NONE;
        rasterDesc.DepthClipEnable = TRUE;
        Com<ID3D11RasterizerState> raster;
        check(device->CreateRasterizerState(&rasterDesc, raster.out()), "CreateRasterizer");
        context->RSSetState(raster.p);
        constexpr unsigned width = 1024, height = 64;
        D3D11_VIEWPORT viewport = {0, 0, float(width), float(height), 0, 1};
        context->RSSetViewports(1, &viewport);
        D3D11_SAMPLER_DESC samplerDesc = {};
        samplerDesc.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
        samplerDesc.AddressU = samplerDesc.AddressV = samplerDesc.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        samplerDesc.MaxLOD = D3D11_FLOAT32_MAX;
        Com<ID3D11SamplerState> sampler;
        check(device->CreateSamplerState(&samplerDesc, sampler.out()), "CreateSampler");
        context->PSSetSamplers(0, 1, &sampler.p);
        unsigned cases = 0;
        for (bool srgb : {false, true}) {
            Com<ID3D11Texture2D> peripheral, focus;
            Com<ID3D11ShaderResourceView> peripheralView, focusView;
            D3D11_TEXTURE2D_DESC sourceDesc = {};
            sourceDesc.Width = sourceDesc.Height = sourceDesc.MipLevels = sourceDesc.ArraySize = 1;
            sourceDesc.Format = srgb ? DXGI_FORMAT_R8G8B8A8_UNORM_SRGB : DXGI_FORMAT_R8G8B8A8_UNORM;
            sourceDesc.SampleDesc.Count = 1; sourceDesc.Usage = D3D11_USAGE_IMMUTABLE;
            sourceDesc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
            uint8_t dark[4] = {64, 64, 64, 255}, light[4] = {192, 192, 192, 255};
            D3D11_SUBRESOURCE_DATA darkData = {dark, 4, 0}, lightData = {light, 4, 0};
            check(device->CreateTexture2D(&sourceDesc, &darkData, peripheral.out()), "CreatePeripheral");
            check(device->CreateTexture2D(&sourceDesc, &lightData, focus.out()), "CreateFocus");
            check(device->CreateShaderResourceView(peripheral.p, nullptr, peripheralView.out()), "CreatePeripheralSRV");
            check(device->CreateShaderResourceView(focus.p, nullptr, focusView.out()), "CreateFocusSRV");
            ID3D11ShaderResourceView* views[] = {peripheralView.p, focusView.p};
            context->PSSetShaderResources(0, 2, views);
            Com<ID3D11Texture2D> target, staging;
            D3D11_TEXTURE2D_DESC desc = {};
            desc.Width = width; desc.Height = height; desc.MipLevels = desc.ArraySize = 1;
            desc.Format = srgb ? DXGI_FORMAT_R8G8B8A8_UNORM_SRGB : DXGI_FORMAT_R32G32B32A32_FLOAT;
            desc.SampleDesc.Count = 1; desc.Usage = D3D11_USAGE_DEFAULT;
            desc.BindFlags = D3D11_BIND_RENDER_TARGET;
            check(device->CreateTexture2D(&desc, nullptr, target.out()), "CreateTarget");
            Com<ID3D11RenderTargetView> targetView;
            check(device->CreateRenderTargetView(target.p, nullptr, targetView.out()), "CreateRTV");
            context->OMSetRenderTargets(1, &targetView.p, nullptr);
            desc.Usage = D3D11_USAGE_STAGING; desc.BindFlags = 0; desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            check(device->CreateTexture2D(&desc, nullptr, staging.out()), "CreateStaging");
            for (bool isOriginal : {true, false}) {
                auto& blob = isOriginal ? original : continuous;
                Com<ID3D11PixelShader> pixel;
                check(device->CreatePixelShader(blob.data(), blob.size(), nullptr, pixel.out()), "CreateActualProjectionShader");
                context->PSSetShader(pixel.p, nullptr, 0);
                for (float smoothing : {0.0f, .08f, .20f}) {
                    struct Constants { float smoothing; uint32_t ignoreAlpha, unpremultiplied, debug; } values = {smoothing, 1, 1, 0};
                    D3D11_BUFFER_DESC bufferDesc = {};
                    bufferDesc.ByteWidth = sizeof(values); bufferDesc.Usage = D3D11_USAGE_IMMUTABLE;
                    bufferDesc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
                    D3D11_SUBRESOURCE_DATA constantsData = {&values, 0, 0};
                    Com<ID3D11Buffer> constants;
                    check(device->CreateBuffer(&bufferDesc, &constantsData, constants.out()), "CreateConstants");
                    context->PSSetConstantBuffers(0, 1, &constants.p);
                    float clear[4] = {-1, -1, -1, -1};
                    context->ClearRenderTargetView(targetView.p, clear);
                    context->Draw(3, 0);
                    context->CopyResource(staging.p, target.p);
                    D3D11_MAPPED_SUBRESOURCE mapped = {};
                    check(context->Map(staging.p, 0, D3D11_MAP_READ, 0, &mapped), "Readback");
                    float maximumError = 0;
                    for (unsigned y = 0; y < height; ++y) {
                        auto row = (uint8_t*)mapped.pData + y * mapped.RowPitch;
                        for (unsigned x = 0; x < width; ++x) {
                            float u = (float(x) + .5f) / width * 1.5f - .25f;
                            float v = (float(y) + .5f) / height * 1.5f - .25f;
                            float alpha = coverage(u, v, smoothing, isOriginal);
                            float a = 64 / 255.0f, b = 192 / 255.0f;
                            if (srgb) { a = decode(a); b = decode(b); }
                            float expected = b * alpha + a * (1 - alpha);
                            if (srgb) expected = encode(expected);
                            for (unsigned channel = 0; channel < 4; ++channel) {
                                float actual = srgb ? row[x * 4 + channel] / 255.0f : ((float*)row)[x * 4 + channel];
                                float error = std::abs(actual - (channel == 3 ? 1.0f : expected));
                                if (!std::isfinite(actual)) throw std::runtime_error("Nonfinite readback");
                                maximumError = std::max(maximumError, error);
                            }
                        }
                    }
                    context->Unmap(staging.p, 0);
                    float tolerance = srgb ? 2.0f / 255 : .00005f;
                    std::printf("%s shader, smoothing %.2f, %s: max error %.8f\n",
                        isOriginal ? "original" : "continuous", smoothing, srgb ? "sRGB" : "float", maximumError);
                    if (maximumError > tolerance) throw std::runtime_error("Coverage/color readback mismatch");
                    ++cases;
                }
            }
        }
        std::printf("FEATHER_PROBE_PASS {\"cases\":%u,\"shader_sha256\":[\"%s\",\"%s\"]}\n",
            cases, hash(original).c_str(), hash(continuous).c_str());
        return 0;
    } catch (const std::exception& error) {
        std::printf("FAIL: %s\n", error.what());
        return 1;
    }
}
