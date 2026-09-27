# Current findings

- Goal evidence depends on preserved Git ancestry. CI fetches full history; squash/rebase merges can still discard the verified commit and make DONE evidence stale. Use merge commits for accepted goals. Support for rewritten history remains a product gap.
- Reopening a goal resets the contract-history scan to its return to `active/`, so verification may warn that the contract changed with product code even when its digest is unchanged. The hardening contract digest is unchanged from its initial commit; history tracing across goal moves remains a product gap.
