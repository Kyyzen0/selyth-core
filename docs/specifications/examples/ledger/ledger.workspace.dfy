// Proof workspace for ledger.obligations.json: money arithmetic in cents, as ledger.ts does it.
//
// Several identifiers share a marker when they belong to one contract. An obligation is
// verified alone, with the `requires` clauses of the others at its marker as hypotheses and
// the clauses written by hand below; a `requires` obligation is verified alone, so the call
// sites below are what proves it. Everything here must verify with every marker blank: the
// bodies are total, and the helper lemma and the loop invariants are proof scaffolding, not
// obligations.

lemma MulMono(a: nat, b: nat, c: nat)
  requires b <= c
  ensures a * b <= a * c
  decreases a
{
  if a > 0 { MulMono(a - 1, b, c); }
}

// ---- Deposit: LED-01 .. LED-03
method Deposit(balance: nat, amount: nat, cap: nat) returns (result: int)
  // selyth:obligation LED-01 LED-02 LED-03
{
  result := balance + amount;
}

method Credit(balance: nat, amount: nat, cap: nat)
  requires balance + amount <= cap
{
  var credited := Deposit(balance, amount, cap);
}

// ---- Withdraw: LED-04 .. LED-06
method Withdraw(balance: nat, amount: nat) returns (result: int)
  // selyth:obligation LED-04 LED-05 LED-06
{
  result := balance - amount;
}

method Spend(balance: nat, price: nat)
  requires price <= balance
{
  var left := Withdraw(balance, price);
}

// ---- Fee in basis points: LED-07 .. LED-09
method Fee(amount: nat, bps: nat) returns (fee: nat)
  // selyth:obligation LED-07 LED-08 LED-09
{
  if bps <= 10000 { MulMono(amount, bps, 10000); }
  fee := amount * bps / 10000;
}

method Charge(amount: nat, bps: nat)
  requires bps <= 10000
{
  var charged := Fee(amount, bps);
}

// ---- Clamp: LED-10 .. LED-12
method Clamp(x: int, lo: int, hi: int) returns (result: int)
  // selyth:obligation LED-10 LED-11 LED-12
{
  if x < lo {
    result := lo;
  } else if x > hi {
    result := hi;
  } else {
    result := x;
  }
}

method Limit(x: int, lo: int, hi: int)
  requires lo <= hi
{
  var limited := Clamp(x, lo, hi);
}

// ---- Splitting a bill: LED-13 .. LED-15
method Share(total: nat, parts: nat) returns (share: nat, remainder: nat)
  // selyth:obligation LED-13 LED-14 LED-15
{
  if parts == 0 {
    share := 0;
    remainder := total;
  } else {
    share := total / parts;
    remainder := total % parts;
  }
}

method SplitBill(total: nat, people: nat)
  requires people > 0
{
  var share, remainder := Share(total, people);
}

// ---- Accruing a fixed amount per period: LED-16 .. LED-19
method Accrue(balance: nat, step: nat, periods: nat) returns (total: nat)
  // selyth:obligation LED-19
{
  total := balance;
  var i := 0;
  while i < periods
    invariant 0 <= i <= periods && total == balance + i * step
    // selyth:obligation LED-16 LED-17 LED-18
  {
    total := total + step;
    i := i + 1;
  }
}

// ---- Moving money between two accounts: LED-20
method Transfer(from: nat, to: nat, amount: nat) returns (fromAfter: int, toAfter: int)
  // selyth:obligation LED-20
{
  fromAfter := from - amount;
  toAfter := to + amount;
}
