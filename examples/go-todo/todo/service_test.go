package todo

import (
	"errors"
	"testing"
)

// TestCreate 验证公开任务契约。 TestCreate checks the public task contract.
func TestCreate(t *testing.T) {
	var service Service
	if _, err := service.Create(" \t\n"); !errors.Is(err, ErrEmptyTitle) {
		t.Fatalf("blank title: got %v, want ErrEmptyTitle", err)
	}
	item, err := service.Create("  first task  ")
	if err != nil || item.ID != 1 || item.Title != "first task" {
		t.Fatalf("normalized first item: %+v, %v", item, err)
	}
	items := service.List()
	items[0].Title = "external mutation"
	if service.List()[0].Title != "first task" {
		t.Fatal("List exposed internal state")
	}
}

// TestNormalizeTitle 要求纯函数规范化。 TestNormalizeTitle requires pure normalization.
func TestNormalizeTitle(t *testing.T) {
	if got := NormalizeTitle(" \t task \n"); got != "task" {
		t.Fatalf("got %q, want task", got)
	}
}
