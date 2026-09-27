# Current findings

- Goal evidence depends on preserved Git ancestry. CI now fetches full history; squash/rebase merges can still discard the verified commit and make DONE evidence stale. Use a merge commit for this goal. Support for rewritten history remains a product gap.
