// Kairo harness for ai-dynamo/nixl @ 464c86388d2c6373ab922d852fa245d81bfb5fb6
//
// Claim under test:
//   nixlAgent::releaseXferReq() returns NIXL_SUCCESS for a POSIX transfer that has
//   not finished, and the transfer is silently truncated. The caller is told the
//   request was released; most of the requested bytes are never written.
//
// Mechanism (all upstream, no mocks):
//   src/plugins/posix/posix_backend.cpp:403  releaseReqH() { delete handle; return SUCCESS; }
//       It never calls io_queue->cancel() and never reports the outstanding work.
//   src/plugins/posix/posix_backend.cpp:141  the queue submits at most
//       MAX_IO_SUBMIT_BATCH_SIZE (64) iocbs per post(); the rest stay queued.
//   src/plugins/posix/linux_aio_io_queue.cpp:181  post() is only reached again via poll(),
//       and poll() is only reached from checkXfer(). Once the handle is deleted there is
//       no handle left to poll, so the queued iocbs are never submitted.
//   src/core/nixl_agent.cpp:1310  releaseXferReq() does exactly one checkXfer(), then
//       releaseReqH(), then reports NIXL_SUCCESS and deletes the handle.
//
// Byte evidence, not an internals assertion: every one of the kDescCount 512 KiB
// regions of the file is filled with its own index byte, so the file itself records
// exactly which descriptors were written.
//
//   bug arm:     post -> releaseXferReq -> read file. Expect NIXL_SUCCESS and a
//                truncated file.
//   control arm: post -> poll getXferStatus until NIXL_SUCCESS -> releaseXferReq ->
//                read file. Expect NIXL_SUCCESS and a complete file.
//
// One round per process: in the bug arm the deleted handle is still the callback
// context of the queued iocbs, so any later poll would be a use-after-free. The
// driver runs one process per round so no measurement is taken after that.

#include <fcntl.h>
#include <unistd.h>
#include <cstring>
#include <cstdio>
#include <cstdint>
#include <string>
#include <vector>
#include <thread>
#include <chrono>
#include <iostream>

#include "nixl.h"
#include "nixl_types.h"
#include "nixl_descriptors.h"

namespace {

constexpr size_t kDescLen = 512ull << 10;   // 512 KiB per descriptor
constexpr int kSubmitBatch = 64;            // MAX_IO_SUBMIT_BATCH_SIZE, linux_aio_io_queue.cpp:25

size_t g_desc_count = 512;                  // overridden from argv[3]
size_t kBufSize() { return kDescLen * g_desc_count; }

void fill(void *p, size_t n, uint8_t v) { std::memset(p, v, n); }

} // namespace

int main(int argc, char **argv) {
    if (argc < 3) {
        std::cerr << "usage: " << argv[0] << " <bug|control> <file> [descriptors]\n";
        return 2;
    }
    const std::string mode = argv[1];
    const char *path = argv[2];
    if (argc > 3) g_desc_count = static_cast<size_t>(std::atol(argv[3]));
    if (g_desc_count == 0) { std::cerr << "descriptors must be > 0\n"; return 2; }
    const size_t kBufSize = kDescLen * g_desc_count;
    const size_t kDescCount = g_desc_count;
    const char *agent_name = "release-probe";

    void *dram = nullptr;
    if (posix_memalign(&dram, 4096, kBufSize) != 0) { std::cerr << "memalign\n"; return 2; }

    // Region i is filled with byte (i + 1), so the file records which regions landed.
    for (size_t i = 0; i < kDescCount; ++i) {
        fill(static_cast<uint8_t *>(dram) + i * kDescLen, kDescLen,
             static_cast<uint8_t>((i % 255) + 1));
    }

    int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { perror("open"); return 2; }
    if (ftruncate(fd, kBufSize) != 0) { perror("ftruncate"); return 2; }

    nixlAgentConfig cfg;
    nixl_b_params_t params;
    nixlBackendH *be = nullptr;
    nixlAgent agent(agent_name, cfg);
    if (agent.createBackend("POSIX", params, be) != NIXL_SUCCESS) {
        std::cerr << "createBackend(POSIX) failed\n";
        return 2;
    }

    // One registration per side covering the whole range.
    nixlBlobDesc d;
    d.addr = reinterpret_cast<uintptr_t>(dram);
    d.len = kBufSize;
    d.devId = 0;
    nixl_reg_dlist_t dram_reg(DRAM_SEG);
    dram_reg.addDesc(d);

    nixlBlobDesc f;
    f.addr = 0;
    f.len = kBufSize;
    f.devId = static_cast<uint64_t>(fd);
    nixl_reg_dlist_t file_reg(FILE_SEG);
    file_reg.addDesc(f);

    if (agent.registerMem(dram_reg) != NIXL_SUCCESS) { std::cerr << "dram reg\n"; return 2; }
    if (agent.registerMem(file_reg) != NIXL_SUCCESS) { std::cerr << "file reg\n"; return 2; }

    // The transfer is split into kDescCount descriptors. Only kSubmitBatch of them
    // can be handed to the kernel by the single post() inside postXferReq.
    nixl_xfer_dlist_t dram_xfer(DRAM_SEG);
    nixl_xfer_dlist_t file_xfer(FILE_SEG);
    for (size_t i = 0; i < kDescCount; ++i) {
        nixlBlobDesc ld;
        ld.addr = reinterpret_cast<uintptr_t>(dram) + i * kDescLen;
        ld.len = kDescLen;
        ld.devId = 0;
        dram_xfer.addDesc(ld);

        nixlBlobDesc lf;
        lf.addr = i * kDescLen;
        lf.len = kDescLen;
        lf.devId = static_cast<uint64_t>(fd);
        file_xfer.addDesc(lf);
    }

    nixlXferReqH *req = nullptr;
    nixl_status_t st = agent.createXferReq(NIXL_WRITE, dram_xfer, file_xfer, agent_name, req);
    if (st != NIXL_SUCCESS) { std::cerr << "createXferReq " << st << "\n"; return 2; }

    st = agent.postXferReq(req);
    const nixl_status_t post_status = st;
    if (st < 0) { std::cerr << "postXferReq " << st << "\n"; return 2; }

    nixl_status_t poll_status = NIXL_ERR_NOT_POSTED;
    if (mode == "control") {
        do {
            poll_status = agent.getXferStatus(req);
        } while (poll_status == NIXL_IN_PROG);
    }

    const nixl_status_t release_status = agent.releaseXferReq(req);

    // No NIXL call from here on in the bug arm: the handle is gone and the queued
    // iocbs still point at it.
    std::this_thread::sleep_for(std::chrono::milliseconds(1200));
    fsync(fd);

    std::vector<uint8_t> got(kBufSize, 0);
    ssize_t n = pread(fd, got.data(), kBufSize, 0);
    if (n < 0) { perror("pread"); return 2; }

    size_t regions_written = 0, bytes_written = 0;
    for (size_t i = 0; i < kDescCount; ++i) {
        const uint8_t want = static_cast<uint8_t>((i % 255) + 1);
        const uint8_t *p = got.data() + i * kDescLen;
        if (std::memcmp(p, p + 0, 1) == 0 && p[0] == want) {
            // Verify the whole region, not just the first byte.
            bool ok = true;
            for (size_t k = 0; k < kDescLen; ++k) {
                if (p[k] != want) { ok = false; break; }
            }
            if (ok) { ++regions_written; bytes_written += kDescLen; }
        }
    }

    std::printf("{\"mode\":\"%s\",\"descriptors\":%zu,\"post\":%d,\"poll\":%d,"
                "\"release\":%d,\"regions_written\":%zu,\"regions_missing\":%zu,"
                "\"bytes_written\":%zu,\"bytes_requested\":%zu}\n",
                mode.c_str(), kDescCount, (int)post_status, (int)poll_status,
                (int)release_status, regions_written, kDescCount - regions_written,
                bytes_written, kBufSize);

    close(fd);
    unlink(path);
    free(dram);
    return 0;
}
