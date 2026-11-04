# here-documents, expanded and quoted, whole or fed line by line
name=moy
cat <<END
hello $name
  $((1 + 2))
END
cat <<'END'
not $name
END
cat <<-END | wc -l
	one
	two
	END
