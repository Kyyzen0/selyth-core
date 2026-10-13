// Proof workspace for docs/specifications/examples/queue.obligations.json.
//
// Each `// selyth:obligation <ID>` marker sits where Dafny expects contract
// clauses (before a method body or a loop body). The backend verifies one
// obligation at a time: it replaces that obligation's marker with its lowered
// clause and deletes the other markers. Clauses written by hand stay.
// A model of the queue: the TypeScript implementation is out of scope for this
// milestone, so its observable state is modelled by a datatype.
datatype Queue = Queue(length: nat, size: nat, capacity: nat)

method Enqueue(l: Queue) returns (result: Queue)
  ensures result.length == l.length + 1
  // selyth:obligation OBL-0007
{
  result := Queue(l.length + 1, l.size, l.capacity);
}

method Fill(capacity: nat) returns (size: nat)
{
  size := 0;
  while size < capacity
    decreases capacity - size
    // selyth:obligation OBL-0008
  {
    size := size + 1;
  }
}

method Drain(start: Queue)
{
  var queue := start;
  while queue.length > 0
    // selyth:obligation OBL-0009
  {
    queue := Queue(queue.length - 1, queue.size, queue.capacity);
  }
}
