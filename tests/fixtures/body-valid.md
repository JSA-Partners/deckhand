### Story

As a guest user, I want to see only the collections I was granted, so that I am not exposed to other organizations' data.

### Scope

#### In

- `GET /v1/collections` filtering for guest principals; the response shape is unchanged
- Store method to list collections by grant

#### Out

- Admin UI for managing grants
- Grants for non-guest roles

### Acceptance Criteria

- Given a guest with one grant, when they list collections, then only that collection is returned
- Given a guest with no grants, when they list collections, then the list is empty
- Given a member, when they list collections, then every collection is returned as before

### Plan

# Guest Collections Implementation Plan

**Goal:** Filter the collection list by grant for guests.

### Task 1: Store method

**Files:**
- Modify: `internal/store/collection.go:40-80`
- Test: `internal/store/collection_test.go`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: List collections by grant**

### Task 2: Handler

**Files:**
- Modify: `internal/server/handlers/collections/list.go`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Filter the list for guest principals**

### Notes

- Grants live in `internal/store/grant.go`
