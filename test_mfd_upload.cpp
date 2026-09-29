// Verify the DCS atlas repair through a real D3D11 upload and GPU readback.
#include <d3d11.h>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <vector>

static std::vector<uint8_t> read(const char* path) {
  std::ifstream file(path, std::ios::binary);
  return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}

int main(int argc, char** argv) {
  if (argc != 3) return 2;
  auto opaque = read(argv[1]);
  auto golden = read(argv[2]);
  if (opaque.size() != 512 * 64 * 4 || golden.size() != opaque.size()) return 2;
  ID3D11Device* device = nullptr;
  ID3D11DeviceContext* context = nullptr;
  auto hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
    nullptr, 0, D3D11_SDK_VERSION, &device, nullptr, &context);
  if (FAILED(hr)) { std::printf("CreateDevice failed: %lx\n", hr); return 3; }
  for (bool unrelated : {false, true}) {
    if (unrelated) opaque[0] ^= 1;
    std::vector<std::vector<uint8_t>> inputs(4), expected(4);
    inputs[0] = opaque;
    expected[0] = unrelated ? opaque : golden;
    unsigned width = 512, height = 64;
    D3D11_SUBRESOURCE_DATA initial[4] = {};
    for (unsigned mip = 0; mip < 4; ++mip) {
      initial[mip].pSysMem = inputs[mip].data();
      initial[mip].SysMemPitch = width * 4;
      if (mip != 3) {
        auto nextWidth = width / 2, nextHeight = height / 2;
        inputs[mip + 1].assign(nextWidth * nextHeight * 4, 255);
        expected[mip + 1] = inputs[mip + 1];
        for (unsigned y = 0; y < nextHeight; ++y)
          for (unsigned x = 0; x < nextWidth; ++x) {
            unsigned sum = 0;
            for (unsigned dy = 0; dy < 2; ++dy)
              for (unsigned dx = 0; dx < 2; ++dx)
                sum += expected[mip][((y * 2 + dy) * width + x * 2 + dx) * 4 + 3];
            expected[mip + 1][(y * nextWidth + x) * 4 + 3] = (sum + 2) / 4;
          }
        width = nextWidth; height = nextHeight;
      }
    }
    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = 512; desc.Height = 64; desc.MipLevels = 4; desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.SampleDesc.Count = 1; desc.Usage = D3D11_USAGE_IMMUTABLE;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    ID3D11Texture2D *texture = nullptr, *staging = nullptr;
    if (FAILED(device->CreateTexture2D(&desc, initial, &texture))) return 4;
    desc.Usage = D3D11_USAGE_STAGING; desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    if (FAILED(device->CreateTexture2D(&desc, nullptr, &staging))) return 4;
    context->CopyResource(staging, texture);
    width = 512; height = 64;
    for (unsigned mip = 0; mip < 4; ++mip) {
      D3D11_MAPPED_SUBRESOURCE mapped = {};
      if (FAILED(context->Map(staging, mip, D3D11_MAP_READ, 0, &mapped))) return 5;
      for (unsigned y = 0; y < height; ++y) {
        auto row = static_cast<uint8_t*>(mapped.pData) + y * mapped.RowPitch;
        if (std::memcmp(row, expected[mip].data() + y * width * 4, width * 4)) {
          std::printf("Readback mismatch: unrelated=%d mip=%u row=%u\n", unrelated, mip, y);
          return 6;
        }
      }
      context->Unmap(staging, mip);
      width /= 2; height /= 2;
    }
    staging->Release(); texture->Release();
    std::printf("PASS: %s; four mip levels match GPU readback\n",
      unrelated ? "unknown fingerprint remains unchanged" : "known atlas alpha is repaired");
  }
  context->Release(); device->Release();
  return 0;
}
