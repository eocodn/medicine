"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

function policyContext() {
  const context = {};
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../../ui/dist/mutation-invariants.js"), "utf8"),
    context,
  );
  return context;
}

test("mutation failure classification keeps only ambiguous outcomes recoverable", () => {
  const context = policyContext();
  assert.equal(vm.runInContext(`MutationInvariants.failureKind({ status: 400 })`, context), "definitive");
  assert.equal(vm.runInContext(`MutationInvariants.failureKind({ status: 409 })`, context), "definitive");
  assert.equal(vm.runInContext(`MutationInvariants.failureKind({ status: 500 })`, context), "ambiguous");
  assert.equal(vm.runInContext(`MutationInvariants.failureKind({ status: 0 })`, context), "ambiguous");
  assert.equal(vm.runInContext(`MutationInvariants.failureKind({})`, context), "ambiguous");
});

test("mutation policy owns profile origin and scheduled dose convergence rules", () => {
  const context = policyContext();
  assert.equal(vm.runInContext(`MutationInvariants.isActiveOrigin("A", "A")`, context), true);
  assert.equal(vm.runInContext(`MutationInvariants.isActiveOrigin("B", "A")`, context), false);
  assert.equal(vm.runInContext(`MutationInvariants.isDoseDesiredStatus("planned")`, context), true);
  assert.equal(vm.runInContext(`MutationInvariants.isDoseDesiredStatus("taken")`, context), true);
  assert.equal(vm.runInContext(`MutationInvariants.isDoseDesiredStatus("skipped")`, context), true);
  assert.equal(vm.runInContext(`MutationInvariants.isDoseDesiredStatus("canceled")`, context), false);
  assert.equal(
    vm.runInContext(`MutationInvariants.doseConverged({ status: "taken" }, "taken")`, context),
    true,
  );
  assert.equal(
    vm.runInContext(`MutationInvariants.doseConverged({ status: "taken" }, "skipped")`, context),
    false,
  );
  assert.equal(
    vm.runInContext(`MutationInvariants.doseConverged({ deleted: true, status: "canceled" }, "planned")`, context),
    true,
  );
});

test("ambiguous scheduled dose recovery has one compensation attempt", () => {
  const context = policyContext();
  assert.equal(vm.runInContext(`MutationInvariants.canCompensateAmbiguousDose(0)`, context), true);
  assert.equal(vm.runInContext(`MutationInvariants.canCompensateAmbiguousDose(1)`, context), false);
  assert.equal(vm.runInContext(`MutationInvariants.MAX_AMBIGUOUS_DOSE_COMPENSATIONS`, context), 1);
});