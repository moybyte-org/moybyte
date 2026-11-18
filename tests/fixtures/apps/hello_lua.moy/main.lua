-- A Lua command: its arguments, a line of stdin, an exit code.
function main(argv)
  print("hello", table.concat(argv, " ", 2))
  local name = input("who? ")
  if name == nil then
    error("no name")
  end
  print("hi", name)
  return 3
end
