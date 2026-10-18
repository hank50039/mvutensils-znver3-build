from pathlib import Path

CPP = Path("MPCVideoRenderer/Source/DX11VideoProcessor.cpp")
raw = CPP.read_bytes()
has_bom = raw.startswith(b"\xef\xbb\xbf")
newline = "\r\n" if b"\r\n" in raw else "\n"
source = raw.decode("utf-8-sig").replace("\r\n", "\n")


def replace_once(old: str, new: str, label: str) -> None:
    global source
    count = source.count(old)
    if count != 1:
        raise SystemExit("{}: expected exactly one anchor, found {}".format(label, count))
    source = source.replace(old, new, 1)


replace_once(
'''HRESULT CDX11VideoProcessor::MemCopyToTexSrcVideo(const BYTE* srcData, const int srcPitch)
{
\tHRESULT hr = S_FALSE;
\tD3D11_MAPPED_SUBRESOURCE mappedResource = {};
''',
'''HRESULT CDX11VideoProcessor::MemCopyToTexSrcVideo(const BYTE* srcData, const int srcPitch)
{
    const auto diagMemCopyStart = std::chrono::steady_clock::now();
    long long diagMapYUs = 0, diagCopyYUs = 0, diagUnmapYUs = 0;
    long long diagMapUVUs = 0, diagCopyUVUs = 0, diagUnmapUVUs = 0;
    long long diagMapVUs = 0, diagCopyVUs = 0, diagUnmapVUs = 0;
    long long diagMapSingleUs = 0, diagCopySingleUs = 0, diagUnmapSingleUs = 0;

\tHRESULT hr = S_FALSE;
\tD3D11_MAPPED_SUBRESOURCE mappedResource = {};
''',
    "MemCopyToTexSrcVideo entry"
)

replace_once(
'''if (m_TexSrcVideo.pTexture2) {
\t\thr = m_pDeviceContext->Map(m_TexSrcVideo.pTexture, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource);
''',
'''if (m_TexSrcVideo.pTexture2) {
        const auto diagMapYStart = std::chrono::steady_clock::now();
\t\thr = m_pDeviceContext->Map(m_TexSrcVideo.pTexture, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource);
        diagMapYUs = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagMapYStart).count();
''',
    "first plane Map timing"
)

replace_once(
'''if (SUCCEEDED(hr)) {
\t\t\tm_pCopyPlaneFn(m_srcHeight, (BYTE*)mappedResource.pData, mappedResource.RowPitch, srcData, srcPitch);
\t\t\tm_pDeviceContext->Unmap(m_TexSrcVideo.pTexture, 0);

\t\t\thr = m_pDeviceContext->Map(m_TexSrcVideo.pTexture2, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource);
''',
'''if (SUCCEEDED(hr)) {
            const auto diagCopyYStart = std::chrono::steady_clock::now();
\t\t\tm_pCopyPlaneFn(m_srcHeight, (BYTE*)mappedResource.pData, mappedResource.RowPitch, srcData, srcPitch);
            diagCopyYUs = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - diagCopyYStart).count();
            const auto diagUnmapYStart = std::chrono::steady_clock::now();
\t\t\tm_pDeviceContext->Unmap(m_TexSrcVideo.pTexture, 0);
            diagUnmapYUs = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - diagUnmapYStart).count();

            const auto diagMapUVStart = std::chrono::steady_clock::now();
\t\t\thr = m_pDeviceContext->Map(m_TexSrcVideo.pTexture2, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource);
            diagMapUVUs = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - diagMapUVStart).count();
''',
    "Y copy, unmap, and UV Map timing"
)

replace_once(
'''srcData += srcPitch * m_srcHeight;
\t\t\t\tm_pCopyPlaneFn(cromaH, (BYTE*)mappedResource.pData, mappedResource.RowPitch, srcData, cromaPitch);
\t\t\t\tm_pDeviceContext->Unmap(m_TexSrcVideo.pTexture2, 0);
''',
'''srcData += srcPitch * m_srcHeight;
                const auto diagCopyUVStart = std::chrono::steady_clock::now();
\t\t\t\tm_pCopyPlaneFn(cromaH, (BYTE*)mappedResource.pData, mappedResource.RowPitch, srcData, cromaPitch);
                diagCopyUVUs = std::chrono::duration_cast<std::chrono::microseconds>(
                    std::chrono::steady_clock::now() - diagCopyUVStart).count();
                const auto diagUnmapUVStart = std::chrono::steady_clock::now();
\t\t\t\tm_pDeviceContext->Unmap(m_TexSrcVideo.pTexture2, 0);
                diagUnmapUVUs = std::chrono::duration_cast<std::chrono::microseconds>(
                    std::chrono::steady_clock::now() - diagUnmapUVStart).count();
''',
    "UV copy and Unmap timing"
)

replace_once(
'''if (m_TexSrcVideo.pTexture3) {
\t\t\t\t\thr = m_pDeviceContext->Map(m_TexSrcVideo.pTexture3, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource);
''',
'''if (m_TexSrcVideo.pTexture3) {
                    const auto diagMapVStart = std::chrono::steady_clock::now();
\t\t\t\t\thr = m_pDeviceContext->Map(m_TexSrcVideo.pTexture3, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource);
                    diagMapVUs = std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now() - diagMapVStart).count();
''',
    "V plane Map timing"
)

replace_once(
'''srcData += cromaPitch * cromaH;
\t\t\t\t\t\tm_pCopyPlaneFn(cromaH, (BYTE*)mappedResource.pData, mappedResource.RowPitch, srcData, cromaPitch);
\t\t\t\t\t\tm_pDeviceContext->Unmap(m_TexSrcVideo.pTexture3, 0);
''',
'''srcData += cromaPitch * cromaH;
                        const auto diagCopyVStart = std::chrono::steady_clock::now();
\t\t\t\t\t\tm_pCopyPlaneFn(cromaH, (BYTE*)mappedResource.pData, mappedResource.RowPitch, srcData, cromaPitch);
                        diagCopyVUs = std::chrono::duration_cast<std::chrono::microseconds>(
                            std::chrono::steady_clock::now() - diagCopyVStart).count();
                        const auto diagUnmapVStart = std::chrono::steady_clock::now();
\t\t\t\t\t\tm_pDeviceContext->Unmap(m_TexSrcVideo.pTexture3, 0);
                        diagUnmapVUs = std::chrono::duration_cast<std::chrono::microseconds>(
                            std::chrono::steady_clock::now() - diagUnmapVStart).count();
''',
    "V copy and Unmap timing"
)

replace_once(
'''\t} else {
\t\thr = m_pDeviceContext->Map(m_TexSrcVideo.pTexture, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource);
\t\tif (SUCCEEDED(hr)) {
\t\t\tconst BYTE* src = (srcPitch < 0) ? srcData + srcPitch * (1 - (int)m_srcLines) : srcData;
\t\t\tm_pCopyPlaneFn(m_srcLines, (BYTE*)mappedResource.pData, mappedResource.RowPitch, src, srcPitch);
\t\t\tm_pDeviceContext->Unmap(m_TexSrcVideo.pTexture, 0);
''',
'''	} else {
        const auto diagMapSingleStart = std::chrono::steady_clock::now();
\t\thr = m_pDeviceContext->Map(m_TexSrcVideo.pTexture, 0, D3D11_MAP_WRITE_DISCARD, 0, &mappedResource);
        diagMapSingleUs = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - diagMapSingleStart).count();
\t\tif (SUCCEEDED(hr)) {
\t\t\tconst BYTE* src = (srcPitch < 0) ? srcData + srcPitch * (1 - (int)m_srcLines) : srcData;
            const auto diagCopySingleStart = std::chrono::steady_clock::now();
\t\t\tm_pCopyPlaneFn(m_srcLines, (BYTE*)mappedResource.pData, mappedResource.RowPitch, src, srcPitch);
            diagCopySingleUs = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - diagCopySingleStart).count();
            const auto diagUnmapSingleStart = std::chrono::steady_clock::now();
\t\t\tm_pDeviceContext->Unmap(m_TexSrcVideo.pTexture, 0);
            diagUnmapSingleUs = std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - diagUnmapSingleStart).count();
''',
    "single-plane Map/copy/Unmap timing"
)

replace_once(
'''\treturn hr;
}

HRESULT CDX11VideoProcessor::SetDevice''',
'''    const long long diagTotalUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - diagMemCopyStart).count();
    const long long diagMapTotalUs = diagMapYUs + diagMapUVUs + diagMapVUs + diagMapSingleUs;
    const long long diagCopyTotalUs = diagCopyYUs + diagCopyUVUs + diagCopyVUs + diagCopySingleUs;
    const long long diagUnmapTotalUs = diagUnmapYUs + diagUnmapUVUs + diagUnmapVUs + diagUnmapSingleUs;
    if (diagTotalUs >= 20000) {
        wchar_t record[1024]{};
        swprintf_s(record, ARRAYSIZE(record),
            L"stage MemCopyToTexSrcVideo sample_start_100ns %lld total_us %lld map_total_us %lld copy_plane_total_us %lld unmap_total_us %lld map_y_us %lld copy_y_us %lld unmap_y_us %lld map_uv_us %lld copy_uv_us %lld unmap_uv_us %lld map_v_us %lld copy_v_us %lld unmap_v_us %lld map_single_us %lld copy_single_us %lld unmap_single_us %lld separate_plane_textures %d src_pitch %d src_lines %d src_height %d hr 0x%08X",
            static_cast<long long>(m_rtStart), diagTotalUs, diagMapTotalUs, diagCopyTotalUs, diagUnmapTotalUs,
            diagMapYUs, diagCopyYUs, diagUnmapYUs, diagMapUVUs, diagCopyUVUs, diagUnmapUVUs,
            diagMapVUs, diagCopyVUs, diagUnmapVUs, diagMapSingleUs, diagCopySingleUs, diagUnmapSingleUs,
            m_TexSrcVideo.pTexture2 ? 1 : 0, srcPitch, m_srcLines, m_srcHeight,
            static_cast<unsigned int>(hr));
        LogMpcvrDx11Diagnostic(record);
    }

\treturn hr;
}

HRESULT CDX11VideoProcessor::SetDevice''',
    "MemCopyToTexSrcVideo summary log"
)

encoded = source.replace("\n", newline).encode("utf-8")
if has_bom:
    encoded = b"\xef\xbb\xbf" + encoded
CPP.write_bytes(encoded)
print("Added per-stage timing for D3D11 Map, CPU plane copy, and Unmap; only slow MemCopyToTexSrcVideo calls are logged.")
