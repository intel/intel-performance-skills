"""Tuning guidance rules keyed by TMA node name."""


GUIDANCE = {
    "Frontend_Bound": {
        "brief": "Pipeline starved for instructions due to frontend issues",
        "suggestions": [
            "Profile with FRONTEND_RETIRED.LATENCY_GE_* for precise fetch stalls",
            "Check code layout: hot functions scattered across pages",
            "Consider PGO/LTO for better code placement",
        ],
        "sample_events": ["FRONTEND_RETIRED.LATENCY_GE_4"],
    },
    "Fetch_Latency": {
        "brief": "Frontend stalled waiting for instructions",
        "suggestions": [
            "Check ITLB misses: large code footprint or scattered jump targets",
            "Check I-cache misses: code too large for L1I",
            "Branch resteers after mispredictions waste frontend cycles",
        ],
        "sample_events": ["FRONTEND_RETIRED.LATENCY_GE_16", "FRONTEND_RETIRED.LATENCY_GE_8"],
    },
    "ICache_Misses": {
        "brief": "Instruction cache misses causing frontend stalls",
        "suggestions": [
            "Reduce code footprint: eliminate dead code, split hot/cold paths",
            "Use PGO to colocate hot functions",
            "Consider -Os optimization for code size",
        ],
        "sample_events": ["FRONTEND_RETIRED.L1I_MISS", "FRONTEND_RETIRED.L2_MISS"],
    },
    "ITLB_Misses": {
        "brief": "Instruction TLB misses",
        "suggestions": [
            "Code footprint exceeds ITLB reach",
            "Consider huge pages for code (2MB pages)",
            "Reduce number of active code pages via PGO",
        ],
        "sample_events": ["FRONTEND_RETIRED.ITLB_MISS", "FRONTEND_RETIRED.STLB_MISS"],
    },
    "Branch_Resteers": {
        "brief": "Frontend resteering after branch events",
        "suggestions": [
            "Reduce branch misprediction rate (see Bad_Speculation)",
            "Reduce branch density in hot loops",
            "Unknown branches (indirect calls) are expensive to resteer",
        ],
        "sample_events": ["BR_MISP_RETIRED.ALL_BRANCHES"],
    },
    "Fetch_Bandwidth": {
        "brief": "Frontend delivering suboptimal bandwidth",
        "suggestions": [
            "Check DSB (decoded stream buffer) coverage",
            "Avoid LCP (length-changing prefixes) in hot code",
            "Ensure hot loops fit in DSB (< 64 uops)",
        ],
        "sample_events": ["FRONTEND_RETIRED.LATENCY_GE_2_BUBBLES_GE_1"],
    },
    "Bad_Speculation": {
        "brief": "Pipeline slots wasted on incorrect speculation",
        "suggestions": [
            "Focus on branch misprediction reduction",
            "Check machine clears (memory ordering, SMC)",
        ],
        "sample_events": [],
        "no_further_hw_observability": False,
    },
    "Branch_Mispredicts": {
        "brief": "Branch misprediction overhead",
        "suggestions": [
            "Profile with BR_MISP_RETIRED.ALL_BRANCHES to find hot mispredicts",
            "Convert unpredictable branches to branchless (cmov, predication)",
            "Consider LLVM HW-PGO: -fprofile-sample-use with branch data (ref: EuroLLVM 2024)",
            "Check if indirect calls (vtables) dominate: consider devirtualization",
        ],
        "sample_events": ["BR_MISP_RETIRED.ALL_BRANCHES"],
        "compiler_suggestion": "LLVM HW-PGO can auto-convert mispredicted branches to CMOV (1.8x on benchmarks)",
    },
    "Machine_Clears": {
        "brief": "Machine clears flushing the pipeline",
        "suggestions": [
            "Check MACHINE_CLEARS.SMC: self-modifying code (JIT invalidation)",
            "Check memory ordering violations in lock-free code",
            "RTM aborts may cause repeated clears",
        ],
        "sample_events": ["MACHINE_CLEARS.COUNT"],
    },
    "Backend_Bound": {
        "brief": "Execution backend cannot retire uops fast enough",
        "suggestions": [
            "Determine if memory-bound or core-bound via L2 breakdown",
        ],
        "sample_events": [],
    },
    "Memory_Bound": {
        "brief": "Stalls due to memory subsystem",
        "suggestions": [
            "Determine cache level causing stalls via L3 breakdown",
            "Check data locality and access patterns",
        ],
        "sample_events": [],
    },
    "L1_Bound": {
        "brief": "L1 data cache causing stalls",
        "suggestions": [
            "Check for cache-unfriendly access patterns (strided, random)",
            "Consider data layout: struct-of-arrays vs array-of-structs",
            "Check fill buffer saturation (L1D_PEND_MISS.FB_FULL)",
            "Address aliasing can cause false dependencies (4K aliasing)",
        ],
        "sample_events": ["MEM_LOAD_RETIRED.L1_MISS", "MEM_LOAD_RETIRED.L1_HIT"],
    },
    "L2_Bound": {
        "brief": "L2 cache misses causing stalls",
        "suggestions": [
            "Working set exceeds L1 but fits in L2",
            "Check HW prefetcher effectiveness (L2_RQSTS.ALL_HWPF)",
            "Consider SW prefetch for predictable patterns",
        ],
        "sample_events": ["MEM_LOAD_RETIRED.L2_MISS"],
    },
    "L3_Bound": {
        "brief": "L3 cache latency causing stalls",
        "suggestions": [
            "Working set exceeds L2, check if it fits in L3",
            "Cross-core sharing (snoops) adds L3 latency",
            "Check for false sharing with perf c2c",
        ],
        "sample_events": ["MEM_LOAD_RETIRED.L3_MISS", "MEM_LOAD_RETIRED.L3_HIT"],
    },
    "DRAM_Bound": {
        "brief": "Memory latency/bandwidth from DRAM",
        "suggestions": [
            "Check NUMA locality: local vs remote DRAM access ratio",
            "Consider memory bandwidth: are you saturating channels?",
            "Optimize data placement: numactl --membind or first-touch",
            "Profile load latency: MEM_TRANS_RETIRED.LOAD_LATENCY_GT_*",
            "Check prefetch effectiveness: are HW prefetches reaching DRAM in time?",
        ],
        "sample_events": ["MEM_LOAD_RETIRED.L3_MISS"],
    },
    "Store_Bound": {
        "brief": "Store operations causing stalls",
        "suggestions": [
            "Store buffer saturation: too many concurrent stores",
            "Check for store-to-load forwarding failures",
            "Consider write-combining for streaming stores (NT stores)",
        ],
        "sample_events": ["MEM_INST_RETIRED.ALL_STORES"],
    },
    "Core_Bound": {
        "brief": "Execution units or scheduler limiting throughput",
        "suggestions": [
            "Check port utilization for imbalanced execution",
            "Divider contention (ARITH.DIVIDER_ACTIVE) if arithmetic heavy",
            "Consider vectorization to increase throughput",
        ],
        "sample_events": ["EXE_ACTIVITY.BOUND_ON_LOADS"],
    },
    "Divider": {
        "brief": "Divider unit contention",
        "suggestions": [
            "Replace divisions with multiplications where possible",
            "Use shift operations for power-of-2 divisions",
            "Consider approximate reciprocal for FP divisions",
        ],
        "sample_events": ["ARITH.DIVIDER_ACTIVE"],
        "no_further_hw_observability": True,
    },
    "Ports_Utilization": {
        "brief": "Suboptimal execution port utilization",
        "suggestions": [
            "Check which ports are saturated vs idle",
            "Reorder independent operations to fill ports",
            "Vectorize to utilize wider execution units",
        ],
        "sample_events": ["EXE_ACTIVITY.1_PORTS_UTIL", "EXE_ACTIVITY.2_PORTS_UTIL"],
    },
    "Retiring": {
        "brief": "Pipeline successfully retiring uops (not a bottleneck)",
        "suggestions": [
            "High Retiring is good — but check if uops/instruction is high",
            "Microcode assists inflate retirement without useful work",
            "Check vectorization: INT_VEC_RETIRED, FP_ARITH_INST_RETIRED",
        ],
        "sample_events": [],
    },
    "Heavy_Operations": {
        "brief": "Retiring heavy (multi-uop) operations",
        "suggestions": [
            "Microcode sequencer (MS) operations are expensive",
            "Check for string operations, CPUID, serializing instructions",
            "Reduce use of complex instructions that decode to many uops",
        ],
        "sample_events": ["UOPS_RETIRED.MS"],
    },
    "Microcode_Sequencer": {
        "brief": "Microcode sequencer generating many uops",
        "suggestions": [
            "Identify MS-heavy instructions (REP MOV, CPUID, etc.)",
            "Replace REP MOVSB with optimized memcpy for known sizes",
            "Check for FP assists (denormals): set FTZ/DAZ flags",
        ],
        "sample_events": ["UOPS_RETIRED.MS"],
    },
    "Light_Operations": {
        "brief": "Efficiently retiring simple operations",
        "suggestions": [
            "This is the ideal state — pipeline is efficient",
            "Check vectorization breadth (FP_Arith, Int_Operations)",
        ],
        "sample_events": [],
    },
}

# Nodes where no PMU event can drill deeper
NO_OBSERVABILITY_NODES = {
    "Divider",
    "LCP",
    "DSB_Switches",
    "MS_Switches",
    "Non_Fused_Branches",
    "Memory_Operations",
    "Fused_Instructions",
}


def get_guidance(node_name: str) -> dict:
    """Get tuning guidance for a TMA node.

    Returns dict with brief, suggestions, sample_events, and flags.
    """
    guidance = GUIDANCE.get(node_name, {
        "brief": f"TMA node: {node_name}",
        "suggestions": ["No specific guidance available for this node"],
        "sample_events": [],
    })

    result = dict(guidance)
    result["no_hw_observability"] = node_name in NO_OBSERVABILITY_NODES

    return result
