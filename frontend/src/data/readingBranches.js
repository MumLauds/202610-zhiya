export function approvedBranchesAt(branches = [], pageNumber) {
  return branches.filter(branch => branch.fromPageNumber === pageNumber && branch.reviewStatus === 'APPROVED')
}
