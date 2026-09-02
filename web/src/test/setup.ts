import "@testing-library/jest-dom/vitest";

// jsdom supplies no native dialog methods/layout. These test state/focus only;
// native containment, sizing and Escape behavior still need browser verification.
if (typeof HTMLDialogElement !== "undefined") {
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
}
if (typeof window !== "undefined") window.scrollTo = () => {};
