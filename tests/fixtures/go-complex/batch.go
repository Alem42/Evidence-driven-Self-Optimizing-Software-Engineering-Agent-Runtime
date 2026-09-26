package ledger

// Batch 在副本中逐项验证；失败时返回原余额。 Batch validates on copies and rolls back on failure.
func Batch(from, to int64, amounts []int64) (int64, int64, error) {
	left, right := from, to
	for _, amount := range amounts {
		var err error
		left, right, err = Transfer(left, right, amount)
		if err != nil {
			return from, to, err
		}
	}
	return left, right, nil
}
