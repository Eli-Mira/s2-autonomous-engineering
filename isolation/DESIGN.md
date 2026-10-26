# S2-01: Execution Confinement Design

## Threat Model

Untrusted model-generated Python code must not:
1. Access host filesystem outside authorized workspace
2. Read environment credentials or API keys
3. Make unauthorized network connections
4. Access sibling task workspaces
5. Modify immutable acceptance tests
6. Spawn excessive processes or exhaust resources
7. Escape via symlinks or path traversal
8. Access Docker socket or host control interfaces

## Available Backends (Evaluated)

### 1. Windows Job Objects + Restricted Tokens
- **Available**: Yes (native Windows API)
- **Pros**: No installation needed, process-level isolation
- **Cons**: No filesystem isolation, limited resource control
- **Verdict**: INSUFFICIENT alone

### 2. WSL2 Containers
- **Available**: Requires WSL2 + Docker integration
- **Pros**: Real process/filesystem isolation
- **Cons**: Requires privileged setup
- **Verdict**: BLOCKED pending authorization

### 3. Docker Containers
- **Available**: Requires Docker daemon
- **Pros**: Full isolation, resource limits, filesystem boundaries
- **Cons**: Requires privileged installation
- **Verdict**: BLOCKED pending authorization

### 4. Python subprocess with Restricted Environment
- **Available**: Yes (stdlib)
- **Pros**: No installation, immediate deployment
- **Cons**: No true filesystem isolation, process-level only
- **Verdict**: BEST AVAILABLE without privileged access

## Recommended Approach: Layered Defense

### Layer 1: Process Isolation (Available Now)
- Run untrusted code in subprocess with restricted environment
- Strip sensitive environment variables
- Set resource limits
- Use subprocess with timeout and process tree termination

### Layer 2: Filesystem Boundary (Available Now)
- Validate all file paths before access
- Reject paths outside authorized workspace
- Reject symlink traversal
- Use os.path.realpath() and verify containment

### Layer 3: Network Boundary (Available Now)
- Disable network by default
- Allow only explicitly approved endpoints

### Layer 4: Container Isolation (Blocked)
- Docker/gVisor for true isolation
- Requires owner authorization

## Implementation Plan

### Phase 1: Subprocess Confinement (IMMEDIATE)
Run untrusted code in subprocess with:
- Restricted environment (only PATH, HOME, TMPDIR)
- Stripped sensitive variables
- Timeout with process tree termination
- Bounded stdout/stderr

### Phase 2: File Access Validation (IMMEDIATE)
- Reject paths outside authorized workspace
- Reject symlink traversal
- Verify containment with realpath()

## Acceptance Criteria
- [ ] Untrusted code cannot read files outside workspace
- [ ] Untrusted code cannot access environment credentials
- [ ] Untrusted code cannot make network connections
- [ ] Resource limits enforced
- [ ] Cleanup after timeout/crash/cancellation
- [ ] Provider gateway remains outside untrusted execution