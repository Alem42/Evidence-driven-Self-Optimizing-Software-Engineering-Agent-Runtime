package fixture

import "testing"

func TestSum(t *testing.T) {
	if Sum(2, 3) != 5 {
		t.Fatal("incorrect sum")
	}
}
