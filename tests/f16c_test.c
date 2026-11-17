/* End-to-end test program. Built WITH F16C, so on a CPU without F16C the
 * unpatched exe dies with "illegal instruction" and the patched one must
 * print PASS. The expected values come from plain integer C code.
 *
 *   gcc -O2 -mavx -mf16c -o f16c_test.exe f16c_test.c
 */
#include <cpuid.h>
#include <immintrin.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define NOINLINE __attribute__((noinline))

static uint32_t ref_h2f(uint16_t h) {
    uint32_t sign = (uint32_t)(h >> 15) << 31, exp = (h >> 10) & 31, mant = h & 0x3FF;
    if (exp == 31) return sign | 0x7F800000u | (mant ? 0x400000u | mant << 13 : 0);
    if (exp == 0) {
        if (!mant) return sign;
        exp = 1;
        while (!(mant & 0x400)) { mant <<= 1; exp--; }
        mant &= 0x3FF;
    }
    return sign | (exp + 112) << 23 | mant << 13;
}

/* mode: 0 nearest-even, 1 down, 2 up, 3 toward zero */
static uint16_t ref_f2h(uint32_t f, int mode) {
    uint32_t sign = f >> 31, exp = (f >> 23) & 255, mant = f & 0x7FFFFF;
    uint16_t s = (uint16_t)(sign << 15);
    int away = (mode == 1 && sign) || (mode == 2 && !sign);
    if (exp == 255) return s | 0x7C00 | (mant ? 0x200 | mant >> 13 : 0);
    if (exp == 0 && mant == 0) return s;
    int e = (int)exp - 127;
    uint64_t m = exp ? (0x800000u | mant) : mant;      /* value = m * 2^(e-23), e=-126 if denormal */
    if (!exp) e = -126;
    if (e > 15) return s | ((mode == 0 || away) ? 0x7C00 : 0x7BFF);
    /* express in units of the target's last place: 2^(max(e,-14)-10) */
    int ulp_exp = (e < -14 ? -14 : e) - 10;
    int shift = ulp_exp - (e - 23);                    /* >= 13 */
    uint64_t q, rem, half;
    if (shift >= 63) { q = 0; rem = 1; half = 2; }
    else { q = m >> shift; rem = m & ((1ull << shift) - 1); half = 1ull << (shift - 1); }
    if (rem) {
        if (mode == 0) { if (rem > half || (rem == half && (q & 1))) q++; }
        else if (away) q++;
    }
    /* q is 0..0x800 in units of ulp; rebuild */
    uint32_t out;
    if (e < -14) out = (uint32_t)q;                    /* subnormal (q==0x400 becomes min normal) */
    else {
        if (q == 0x800) { q = 0x400; e++; }
        if (e > 15) return s | 0x7C00;
        out = (uint32_t)(e + 15) << 10 | ((uint32_t)q & 0x3FF);
    }
    return s | (uint16_t)out;
}

NOINLINE void h2f8(const uint16_t *in, uint32_t *out) {
    _mm256_storeu_ps((float *)out, _mm256_cvtph_ps(_mm_loadu_si128((const __m128i *)in)));
}
NOINLINE void h2f4(const uint16_t *in, uint32_t *out) {
    _mm_storeu_ps((float *)out, _mm_cvtph_ps(_mm_loadl_epi64((const __m128i *)in)));
}
#define F2H(name, imm) \
    NOINLINE void name##_8(const uint32_t *in, uint16_t *out) { \
        _mm_storeu_si128((__m128i *)out, _mm256_cvtps_ph(_mm256_loadu_ps((const float *)in), imm)); } \
    NOINLINE void name##_4(const uint32_t *in, uint16_t *out) { \
        _mm_storel_epi64((__m128i *)out, _mm_cvtps_ph(_mm_loadu_ps((const float *)in), imm)); }
F2H(f2h_m0, 0) F2H(f2h_m1, 1) F2H(f2h_m2, 2) F2H(f2h_m3, 3)

typedef void (*f2h_fn)(const uint32_t *, uint16_t *);
static f2h_fn F8[4] = {f2h_m0_8, f2h_m1_8, f2h_m2_8, f2h_m3_8};
static f2h_fn F4[4] = {f2h_m0_4, f2h_m1_4, f2h_m2_4, f2h_m3_4};

static uint64_t rng_state = 0x9E3779B97F4A7C15ull;
static uint32_t rnd(void) {
    rng_state ^= rng_state << 13; rng_state ^= rng_state >> 7; rng_state ^= rng_state << 17;
    return (uint32_t)(rng_state >> 16);
}

NOINLINE int cpu_says_f16c(void) {
    unsigned a, b, c, d;
    __cpuid(1, a, b, c, d);
    return (c >> 29) & 1;
}

int main(void) {
    long errors = 0, checked = 0;
    printf("CPUID reports F16C: %s\n", cpu_says_f16c() ? "yes" : "NO");
    if (!cpu_says_f16c()) errors++;
    for (uint32_t h = 0; h < 0x10000; h += 8) {
        uint16_t in[8]; uint32_t out[8], out4[4];
        for (int i = 0; i < 8; i++) in[i] = (uint16_t)(h + i);
        h2f8(in, out); h2f4(in, out4);
        for (int i = 0; i < 8; i++, checked++) {
            if (out[i] != ref_h2f(in[i]) || (i < 4 && out4[i] != out[i])) {
                if (errors++ < 10) printf("h2f %04x -> %08x expected %08x\n", in[i], out[i], ref_h2f(in[i]));
            }
        }
    }
    for (int mode = 0; mode < 4; mode++) {
        for (long n = 0; n < 6000000; n++) {
            uint32_t in[8]; uint16_t out[8], out4[4];
            for (int i = 0; i < 8; i++) {
                uint32_t r = rnd();
                switch (n & 3) {
                case 0: in[i] = r; break;                                         /* anything */
                case 1: in[i] = (r & 0x807FFFFF) | (uint32_t)(96 + rnd() % 50) << 23; break; /* half range */
                case 2: in[i] = (r & 0xFFFFE000) | (rnd() & 1 ? 0x1000 : 0x0FFF); break;     /* ties */
                default: in[i] = (r & 0x80000000) | (uint32_t)(n * 8 + i); break;  /* tiny / denormal */
                }
            }
            F8[mode](in, out); F4[mode](in, out4);
            for (int i = 0; i < 8; i++, checked++) {
                uint16_t want = ref_f2h(in[i], mode);
                if (out[i] != want || (i < 4 && out4[i] != want)) {
                    if (errors++ < 10) printf("f2h mode %d %08x -> %04x expected %04x\n", mode, in[i], out[i], want);
                }
            }
        }
    }
    printf("%ld values checked, %ld errors\n%s\n", checked, errors, errors ? "FAIL" : "PASS");
    return errors != 0;
}
