// Kairo harness for ai-dynamo/nixl @ 464c86388d2c6373ab922d852fa245d81bfb5fb6
//
// Claim under test:
//   NIXL normalizes len == 0 to SIZE_MAX for BLK_SEG / OBJ_SEG / FILE_SEG
//   ("whole object from this offset") in exactly one of its two lookup paths.
//
//     src/infra/mem_section.h:131  normalizeSecDesc()  -> len 0 becomes SIZE_MAX on register
//     src/infra/mem_section.h:150  normalizeQuery()    -> len 0 becomes SIZE_MAX in getIndex()
//
//   getIndex() normalizes both sides, so deregisterMem() finds the entry.
//   getCoveringIndex() does not normalize, and nixlBasicDesc::covers() evaluates
//   (addr + len) >= (query.addr + query.len). With len == SIZE_MAX and addr != 0 that
//   add wraps: addr + SIZE_MAX == addr - 1. The entry can then never cover anything,
//   so nixlMemSection::populate() cannot resolve it and createXferReq() fails with
//   NIXL_ERR_NOT_FOUND.
//
//   Net effect for a file segment, where addr is the byte offset in the file:
//   a registration made with len == 0 is removable but never transferable,
//   unless its offset happens to be 0.
//
// One variable under test: the file offset. Same backend, same file, same len == 0,
// same transfer. Each cell runs in its own agent against a fresh file.
//
//   A  len=0   offset 0      transfer expected to succeed   (control)
//   B  len=0   offset 4096   transfer expected to fail      (claim)
//   C  len=4096 offset 4096  transfer expected to succeed   (control)
//   D  len=0   offset 4096   deregistration expected to succeed (asymmetry)
//
// Byte evidence: any cell that reports transfer success also has its 4 KiB payload
// read back off disk and compared.

#include <fcntl.h>
#include <unistd.h>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include <iostream>

#include "nixl.h"
#include "nixl_types.h"
#include "nixl_descriptors.h"

namespace {

constexpr size_t kLen = 4096;
constexpr uint8_t kPayload = 0x3C;
constexpr char kAgentName[] = "unbounded-len-probe";

int fd = -1;

struct Cell {
    const char *name;
    size_t reg_len;
    size_t offset;
    size_t reg_end;
    size_t xfer_end;
    bool covered;
    nixl_status_t register_status;
    nixl_status_t create_status;
    bool bytes_verified;
    nixl_status_t dereg_status;
};

Cell run_cell(const char *name, size_t reg_len, size_t offset, const char *path) {
    Cell c{};
    c.name = name;
    c.reg_len = reg_len;
    c.offset = offset;
    // The registration is the whole object from `offset`, so when the caller asks
    // for the unbounded form it spans [offset, offset + SIZE_MAX) and the 4 KiB
    // transfer sits inside it. With an explicit length it spans [offset, reg_end).
    const size_t reg_end =
        (reg_len == 0) ? SIZE_MAX : offset + reg_len;
    c.reg_end = reg_end;
    c.xfer_end = offset + kLen;
    // Containment is measured from the addresses the cell actually used, never
    // asserted by the caller.
    c.covered = (offset <= c.xfer_end) && (c.xfer_end <= reg_end);
    c.register_status = NIXL_ERR_NOT_POSTED;
    c.create_status = NIXL_ERR_NOT_POSTED;
    c.bytes_verified = false;
    c.dereg_status = NIXL_ERR_NOT_POSTED;

    if (ftruncate(fd, 0) != 0) { perror("ftruncate"); exit(2); }

    void *dram = nullptr;
    if (posix_memalign(&dram, 4096, kLen) != 0) { std::cerr << "memalign\n"; exit(2); }
    std::memset(dram, kPayload, kLen);

    nixlAgentConfig cfg;
    nixl_b_params_t params;
    nixlBackendH *be = nullptr;
    nixlAgent agent(kAgentName, cfg);
    if (agent.createBackend("POSIX", params, be) != NIXL_SUCCESS) {
        std::cerr << "createBackend(POSIX)\n"; exit(2);
    }

    nixlBlobDesc d;
    d.addr = reinterpret_cast<uintptr_t>(dram);
    d.len = kLen;
    d.devId = 0;
    nixl_reg_dlist_t dram_reg(DRAM_SEG);
    dram_reg.addDesc(d);

    nixlBlobDesc f;
    f.addr = offset;
    f.len = reg_len; // the variable under test
    f.devId = static_cast<uint64_t>(fd);
    nixl_reg_dlist_t file_reg(FILE_SEG);
    file_reg.addDesc(f);

    c.register_status = agent.registerMem(dram_reg);
    if (c.register_status == NIXL_SUCCESS)
        c.register_status = agent.registerMem(file_reg);

    if (c.register_status == NIXL_SUCCESS) {
        nixl_xfer_dlist_t dram_xfer(DRAM_SEG);
        dram_xfer.addDesc(d);

        nixl_xfer_dlist_t file_xfer(FILE_SEG);
        nixlBlobDesc q = f;
        q.len = kLen; // every transfer asks for 4 KiB at this offset
        file_xfer.addDesc(q);

        nixlXferReqH *req = nullptr;
        c.create_status = agent.createXferReq(NIXL_WRITE, dram_xfer, file_xfer, kAgentName, req);
        if (c.create_status == NIXL_SUCCESS) {
            nixl_status_t s = agent.postXferReq(req);
            do { s = agent.getXferStatus(req); } while (s == NIXL_IN_PROG);
            agent.releaseXferReq(req);
            if (s == NIXL_SUCCESS) {
                if (fsync(fd) != 0) { perror("fsync"); exit(2); }
                std::vector<uint8_t> got(kLen, 0);
                bool ok = pread(fd, got.data(), kLen, static_cast<off_t>(offset)) ==
                          static_cast<ssize_t>(kLen);
                for (size_t i = 0; ok && i < kLen; ++i)
                    if (got[i] != kPayload) ok = false;
                c.bytes_verified = ok;
            }
        }
    }

    // Same descriptor that was registered, used verbatim to deregister.
    c.dereg_status = agent.deregisterMem(file_reg);

    free(dram);
    (void)path;
    return c;
}

} // namespace

int main() {
    const char *path = "/tmp/kairo_nixl_unbounded_len.bin";
    fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { perror("open"); return 2; }

    const Cell cells[] = {
        run_cell("A_len0_offset0",     0,    0, path),
        run_cell("B_len0_offset4096",  0,    kLen, path),
        run_cell("C_len4096_offset4096", kLen, kLen, path),
        run_cell("D_len0_offset4096_dereg_only", 0, kLen, path),
    };

    for (const Cell &c : cells) {
        const std::string reg_end_str =
            c.reg_end == SIZE_MAX ? std::string("SIZE_MAX") : std::to_string(c.reg_end);
        std::printf(
            "{\"cell\":\"%s\",\"reg_len\":%zu,\"offset\":%zu,\"reg_end\":\"%s\","
            "\"xfer_end\":%zu,\"covered\":%s,\"register\":%d,\"createXferReq\":%d,"
            "\"bytes_verified\":%s,\"deregister\":%d}\n",
            c.name, c.reg_len, c.offset, reg_end_str.c_str(), c.xfer_end,
            c.covered ? "true" : "false", (int)c.register_status,
            (int)c.create_status, c.bytes_verified ? "true" : "false",
            (int)c.dereg_status);
    }

    // Claim: B registers and deregisters fine but cannot transfer. A and C transfer.
    const bool as_expected =
        cells[0].register_status == NIXL_SUCCESS && cells[0].create_status == NIXL_SUCCESS &&
        cells[0].bytes_verified &&
        cells[1].register_status == NIXL_SUCCESS && cells[1].create_status != NIXL_SUCCESS &&
        cells[2].register_status == NIXL_SUCCESS && cells[2].create_status == NIXL_SUCCESS &&
        cells[2].bytes_verified &&
        cells[3].register_status == NIXL_SUCCESS && cells[3].dereg_status == NIXL_SUCCESS;
    std::printf("VERDICT %s\n", as_expected ? "as_expected" : "NOT_as_expected");

    close(fd);
    unlink(path);
    return as_expected ? 0 : 1;
}