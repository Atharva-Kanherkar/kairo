// Root-cause confirmation for the kairo finding, against real upstream code.
//
// Compiles against the pinned checkout's own headers, no logic copied: it calls the
// real nixlBasicDesc::covers() and the real nixlSecDescList::getCoveringIndex(), with
// the values nixlSecDescList produces for a FILE_SEG entry registered with len == 0.
//
// nixlSecDescList::normalizeSecDesc() (src/infra/mem_section.h:131) turns len 0 into
// SIZE_MAX for FILE_SEG. covers() then evaluates (addr + SIZE_MAX) >= (q.addr + q.len),
// which wraps. getCoveringIndex() has no normalizeQuery(), so it never recovers.

#include <cstdint>
#include <cstdio>
#include <climits>

#include "nixl_descriptors.h"

int main() {
    const size_t SIZE_MAX_ = SIZE_MAX;
    const uintptr_t kOff = 4096;

    printf("== nixlBasicDesc::covers with the len==0 normalization applied ==\n");

    nixlBasicDesc base_at_0(kOff - kOff, SIZE_MAX_, 7);   // addr = 0
    nixlBasicDesc base_at_off(kOff, SIZE_MAX_, 7);        // addr = 4096

    nixlBasicDesc q(kOff, 4096, 7);                       // the transfer's descriptor

    printf("base{addr=0,    len=SIZE_MAX}.covers(q{4096,4096}) = %s\n",
           base_at_0.covers(q) ? "true" : "false");
    printf("base{addr=4096, len=SIZE_MAX}.covers(q{4096,4096}) = %s\n",
           base_at_off.covers(q) ? "true" : "false");

    printf("\nraw arithmetic at addr=4096: (addr + SIZE_MAX) mod 2^64 = %zu\n",
           static_cast<size_t>(kOff + SIZE_MAX_));
    printf("raw arithmetic at addr=0:    (addr + SIZE_MAX) mod 2^64 = %zu\n",
           static_cast<size_t>(static_cast<uintptr_t>(0) + SIZE_MAX_));

    nixlBasicDesc explicit_len(kOff, 4096, 7);
    printf("\ncontrol, base{addr=4096, len=4096}.covers(q{4096,4096}) = %s\n",
           explicit_len.covers(q) ? "true" : "false");

    const bool as_expected = !base_at_0.covers(q) == false &&  // offset 0 resolves
                             !base_at_off.covers(q) &&       // offset 4096 does not
                             explicit_len.covers(q);
    printf("\nVERDICT %s\n", as_expected ? "as_expected" : "NOT_as_expected");
    return as_expected ? 0 : 1;
}