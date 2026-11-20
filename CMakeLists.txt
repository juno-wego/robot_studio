cmake_minimum_required(VERSION 3.22)
project(robot_studio VERSION 0.1.1 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Qt6 6.2 REQUIRED COMPONENTS Widgets)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
include(GNUInstallDirs)

qt_add_executable(wego-jetson app/main.cpp)
target_link_libraries(wego-jetson PRIVATE Qt6::Widgets)
target_compile_options(wego-jetson PRIVATE -Wall -Wextra -Wpedantic)

set(helper_id wego-jetson-backend)
set(helper_path "${CMAKE_BINARY_DIR}/backend/${helper_id}/${helper_id}")
set(assets
    configure-ethernet.py flash-qspi-only.sh postboot.sh robot_stack.sh verify_ros.py
    go2_navigation/mapping.launch.py go2_navigation/slam_params.yaml
)
set(data_flags)
foreach(asset IN LISTS assets)
    get_filename_component(dir "${asset}" DIRECTORY)
    if(NOT dir)
        set(dir ".")
    endif()
    list(APPEND data_flags --add-data "${CMAKE_SOURCE_DIR}/${asset}:${dir}")
endforeach()
add_custom_command(OUTPUT "${helper_path}"
    COMMAND "${Python3_EXECUTABLE}" -m PyInstaller --noconfirm --clean --onedir
        --name "${helper_id}" --distpath "${CMAKE_BINARY_DIR}/backend"
        --workpath "${CMAKE_BINARY_DIR}/freeze" --specpath "${CMAKE_BINARY_DIR}"
        ${data_flags} "${CMAKE_SOURCE_DIR}/installer.py"
    DEPENDS installer.py installer_backend.py installer_support.py installer_host.py ${assets}
    VERBATIM
)
add_custom_target(backend ALL DEPENDS "${helper_path}")
add_dependencies(wego-jetson backend)
file(COPY docs DESTINATION "${CMAKE_BINARY_DIR}")
install(TARGETS wego-jetson RUNTIME DESTINATION bin)
install(DIRECTORY "${CMAKE_BINARY_DIR}/backend/${helper_id}/"
    DESTINATION lib/wego-jetson/backend USE_SOURCE_PERMISSIONS)
install(FILES docs/usage.html docs/navigation.html DESTINATION share/doc/wego-jetson)
install(FILES packaging/wego-jetson-installer.desktop DESTINATION share/applications)

enable_testing()
add_test(NAME safety COMMAND "${Python3_EXECUTABLE}" -m unittest discover -s tests)
set_tests_properties(safety PROPERTIES WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}")
add_test(NAME catalog COMMAND "${helper_path}" catalog)
add_test(NAME startup COMMAND wego-jetson --smoke-test)
set_tests_properties(startup PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

set(CPACK_GENERATOR "DEB")
set(CPACK_PACKAGE_NAME "wego-jetson")
set(CPACK_PACKAGE_VENDOR "WeGo")
set(CPACK_PACKAGE_CONTACT "WeGo <robot-studio@users.noreply.github.com>")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Jetson and robot installation and verification")
set(CPACK_DEBIAN_PACKAGE_SECTION "utils")
set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)
set(CPACK_DEBIAN_PACKAGE_DEPENDS "openssh-client, iproute2, usbutils, gnome-terminal, util-linux, xdg-utils, sudo, fonts-noto-cjk, python3")
set(CPACK_PACKAGE_FILE_NAME "${CPACK_PACKAGE_NAME}_${PROJECT_VERSION}_${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}")
include(CPack)
