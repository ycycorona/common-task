for pid in $(ps -axo pid=,ppid= | awk '$2 == 1 {print $1}'); do
  cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')

  [[ "$cwd" == "$HOME/Code/"* ]] || continue

  ps -p "$pid" -o pid=,ppid=,etime=,command=
done