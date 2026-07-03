file(GLOB LUA_SRCS "${CMAKE_CURRENT_SOURCE_DIR}/*.c")
idf_component_register(SRCS ${LUA_SRCS} INCLUDE_DIRS ".")
target_compile_definitions(${COMPONENT_LIB} PRIVATE LUA_32BITS=0)
target_compile_options(${COMPONENT_LIB} PRIVATE -O2 -Wno-error)
