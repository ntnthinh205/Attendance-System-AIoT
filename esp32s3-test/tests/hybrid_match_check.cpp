#include "hybrid_match.h"
#include <fstream>
#include <iostream>
#include <vector>
int main(int argc,char **argv) {
    if (argc!=2) return 1;
    std::ifstream stream(argv[1],std::ios::binary);
    std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(stream)),{});
    if (!hybrid_core::valid_gallery(bytes.data(),bytes.size())) return 2;
    auto *records=reinterpret_cast<const hybrid_core::Record *>(bytes.data()+sizeof(hybrid_core::Header));
    hybrid_core::Input input={};
    std::copy(records[0].vector,records[0].vector+512,input.vector);
    auto result=hybrid_core::edge_match(input,bytes.data());
    std::cout<<result.status<<" "<<result.id<<"\n";
    if (result.status!="matched" || result.id!="person-a") return 3;
    std::fill(input.vector,input.vector+512,0.f); input.vector[511]=1;
    if (hybrid_core::edge_match(input,bytes.data()).status!="unknown") return 4;
    bytes[0]='X';
    if (hybrid_core::valid_gallery(bytes.data(),bytes.size())) return 5;
    return 0;
}
